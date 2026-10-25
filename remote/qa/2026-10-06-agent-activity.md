# Agent Activity and Live Operation Output

## Scope and Release Boundary

Prepared on the latest source, retaining the 0.1.78 preparation-concurrency change:
macOS 0.1.418 (build 418), pcrd 0.1.79.

No user Agent, synthesis, finalization, message, research directory, private
Codex configuration, Android device, formal client, or Node service was stopped,
resumed, resubmitted, adopted, reconfigured, or restarted. No new follow-up
automation was created. Native checks were offscreen without QA windows or
desktop input. Builds used two low-priority compilation jobs.

The 2026-10-06T11:03:04Z read-only snapshot found the installed formal client
at 0.1.417 and the actual healthy, paired, detached-worker daemon at 0.1.77,
with one active run, no active operation, and empty admission/delivery queues.
Consequently this turn prepares artifacts without activating the new runtime,
installing/restarting the formal app, or advertising a new update prematurely.
The live task cannot acquire new adapter code already loaded by its worker.

## Confirmed Causes

1. Run ownership/lifecycle was used as the displayed activity state. A long
   native wait therefore still appeared as work, even though the original
   no-text `sleep` item supplied a planned duration.
2. The display projection did not recognize no-text native waits. Their start
   and end could be present in the raw event stream but absent from the work
   timeline. Updating status text alone could not restore this missing signal.
3. The app-server adapter did not forward `item/commandExecution/outputDelta`.
   A long-lived command could produce real output before completion without
   that output reaching the app's visible operation details.
4. Details were keyed to one event revision. Cancelling reads whenever a newer
   output revision arrived could starve a continuously producing operation;
   a failed tail could also remain unread without another revision.

These are PCR event/projection/publication problems. They do not establish
that an unrelated training job, GPU scheduler, or server computation stalled.

## Root Changes

- Separate observed activity from lifecycle. Track active calls and native
  waits by identity; expose thinking, executing, responding, waiting, and
  awaiting-next-activity states. Preserve stop/continue/finalization semantics.
- Native wait start/end use one stable operation row. Show a planned deadline,
  actual last observation/output time, and confirmed active tool count. Passing
  a planned deadline never fabricates completion or a guaranteed wake-up.
- Replay existing native wait records with their original timestamps. Explicit
  turn termination clears current activity without inventing a successful
  independent tool completion.
- Forward native command-output deltas and append full UTF-8 chunks to an
  operation output index in retained run metadata, outside candidate workspaces
  and private Codex homes. Preserve original raw native records independently.
- Each event references an immutable byte prefix. Details read bounded pages
  of that prefix, retain already verified content, finish valid in-flight reads,
  and catch up to a newer target without restarting at zero. Transient read
  failures receive bounded backoff; permanent errors retain text and explicit
  retry. Lifecycle termination does not cancel an expanded output read.
- Do not merge completion summaries into a delta stream by guessing overlap.
  A -> summary AB -> late delta B yields AB, not ABB. If a summary-only fallback
  later receives unpositioned deltas with indeterminate coverage, stop only
  the optional index and preserve raw records; never stop the Agent.
- Reject missing/extra indexed prefixes rather than presenting normal EOF or
  altering an already published prefix. Do not claim power-loss durability or
  automatic reconstruction that has not been tested.
- Refresh the selected native reader when operation revisions advance; keep
  row identity, selection, reading position, and the cached prefix. Observation
  clock updates are local to the small status line, not fake transcript events
  or additional research-server polling.
- Unobserved detached-job progress remains explicitly unknown. This release
  does not invent percentages, training-job counts, health, or scheduler data.

## Verification

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentActivity-20261006`.

- Source Python: 1015 tests and 127 subtests passed.
- Installed 0.1.79 runtime: 1014 tests and 127 subtests passed; one source-location
  packaging assertion deselected. Every installed Python module matches the
  immutable wheel, with no source checkout in the installed test import path.
- Background macOS suite initially passed 349 tests in 40 suites. Additional
  live-reader and terminal/availability boundary tests are recorded below after
  the final full run, not inferred from the earlier result.
- Generated conversation contracts pass `--check`; added fields are optional,
  activity state is open-ended, and sleep uses an existing compatible kind.
- The actual installed Codex CLI generated JSON schemas in an isolated evidence
  directory. Its sleep item is `sleep` with `id` and `durationMs`; command output
  deltas contain `threadId`, `turnId`, `itemId`, and `delta`, with no offset or
  source sequence number. No real user turn was submitted for this check.
- Offscreen native rendering passed across all text sizes, Chinese/English,
  360/760-point widths, waiting/executing states. The observation row has actual
  contrasting glyph pixels, not merely a reserved height. Synthetic screenshots
  use an opaque native background and are not evidence of formal-client install.
- Real fixture sockets verify fast successive revisions do not cancel a valid
  prefix, and a transient final-page failure recovers without a new event.
- macOS bundle signing and designated requirement match the established local
  release identity. Strict deep signature and packaged math/syntax smoke checks
  passed. The bundle was not opened as a desktop app.
- Python regressions cover meaningful timestamps, parallel calls/waits,
  invalid duration, replay/attempt boundaries, original wait times, repeated
  text, Unicode pages, terminal late output, overlap ambiguity, index gaps,
  and an optional index error not interrupting the native Agent stream.

Test-harness corrections during verification: use the existing SDK 26.5 and
official background script for Swift Testing macro paths; execute the zsh
packager as zsh, not bash. A mock Agent-preload server needed `SO_NOSIGPIPE`
for intentionally disconnected clients; its SIGPIPE was a test-process failure,
not evidence that the formal app or user Agent crashed. Final successful checks
supersede these failed verification attempts, without hiding their cause.

## Prepared Artifacts

- Wheel: `release/pcrd/parallel_codex_runner-0.1.79-py3-none-any.whl`
  SHA-256: `6d1b6d3f09716517038ebf6d3857af168f402f68ecf8b5675878504b701879d1`.
- Signed client: `release/clients/macos/Parallel-Codex-macos-0.1.418.zip`
  SHA-256: `ff3b1bccd3b5ce9f462bae8633527499076dee1976aff51aef9c6e2f2905f99c`.
- Isolated runtime: `~/Library/Application Support/parallel-codex-runner/runtime/0.1.79`.
- The staged manifest requires macOS build 418 for complete activity presentation.
  It is in the evidence `publication` directory. The advertised release manifest
  and relay catalog are not changed while the existing task is active.
- Previous immutable 0.1.78 wheel, macOS 0.1.417 archive, installer, Android,
  relay artifacts, and their advertised manifest entries remain unchanged.

Native synthetic examples:

![Chinese narrow native activity strip](/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentActivity-20261006/native-render/activity-zh-Hans-360.png)

## Final Verification and Deployment Status

- Final full background macOS run: **352 tests in 40 suites passed** in
  194.729 seconds. Source/installed Python results above remain verified.
- The same native reader instance repainted real socket-delivered output while
  the command remained running; its selected operation and windowless state
  were retained, and the actual glyph bitmap changed without a wait-clock tick.
- A known output prefix completed after the run became terminal. An unavailable
  index retained readable text, displayed a local error/retry, did not re-enter
  an automatic retry loop, and did not change the Agent's real execution state.
- At 2026-10-06T11:23:13Z the actual daemon remained **0.1.77**, healthy and paired,
  with detached workers, one active run, no active operation, and empty
  admission/delivery queues. The formal installed client remained **0.1.417**.
- The formal client retained its baseline PID/start identity. The known Node
  web service retained PID 80080/start identity, still listened on port 5173,
  and returned HTTP 200. No maintenance command stopped or restarted it.
- Do not claim all Node/helper processes survived: 27 baseline entries remained,
  but PIDs 12275 and 12315 were absent in the final snapshot; the latter was a
  ChatGPT CUA helper. The other absence's cause was not established. This was
  not silently changed to a passing whole-process assertion.
- Artifact hashes and the unchanged advertised manifest were rechecked. The
  signed **0.1.418** archive and immutable **0.1.79** wheel/runtime are prepared,
  but this turn has **not published the relay update, installed/restarted the
  formal app, activated pcrd, or created an automatic follow-up**. Activation
  must wait for the user's existing tasks to finish and the compatible client
  to be installed under the user's authorization.

## Verified Publication 2026-10-07

```json
{
  "checked_at_utc": "2026-10-07T06:06:57.356980+00:00",
  "macos_published": "0.1.418",
  "pcrd_published": "0.1.79",
  "macos_archive_sha256": "ff3b1bccd3b5ce9f462bae8633527499076dee1976aff51aef9c6e2f2905f99c",
  "pcrd_wheel_sha256": "6d1b6d3f09716517038ebf6d3857af168f402f68ecf8b5675878504b701879d1",
  "relay_catalog_verified": true,
  "unrelated_artifacts_preserved": true,
  "actual_scheduler_version": "0.1.77",
  "actual_client_version": [
    "0.1.417",
    "417"
  ],
  "qa_windows_opened": false
}
```

## Verified Client Installation 2026-10-07

```json
{
  "checked_at_utc": "2026-10-07T06:08:06.209297+00:00",
  "macos_installed_and_running": "0.1.418",
  "build": "418",
  "loaded_executable_inode_verified": true,
  "client_pid": 30884,
  "builtin_updater_used": true,
  "background_relaunch": true,
  "signature_verified": true,
  "rollback_performed": false,
  "pcrd_not_restarted_during_client_install": true,
  "node_services_preserved": true,
  "frontmost_pid_before": 99245,
  "frontmost_pid_after": 99245,
  "qa_windows_opened": false
}
```

## Activation 2026-10-07T06:08:41.460319+00:00

```json
{
  "actual_version": "0.1.79",
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
  "scheduler_pid_before": 81753,
  "scheduler_pid_after": 31097,
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
  "checked_at_utc": "2026-10-07T06:08:41.460319+00:00"
}
```

## Verified Deployment 2026-10-07

```json
{
  "checked_at_utc": "2026-10-07T06:09:01.990529+00:00",
  "macos": "0.1.418",
  "pcrd": "0.1.79",
  "client_pid": 30884,
  "healthy": true,
  "paired": true,
  "detached_workers": true,
  "gpt_6_1_sol_available": true,
  "activation_after_global_idle": true,
  "admission_and_delivery_empty_before_switch": true,
  "node_service_pids_preserved": [
    "80080",
    "92110"
  ],
  "node_listeners_preserved": true,
  "client_not_restarted_by_backend_activation": true,
  "no_tasks_stopped_or_resubmitted": true,
  "qa_windows_opened": false,
  "new_followup_created": false,
  "active_run_count_now": 0,
  "active_operation_now": false
}
```

## Final Live Availability 2026-10-07

```json
{
  "checked_at_utc": "2026-10-07T06:09:20.598638+00:00",
  "macos_build": 418,
  "newer_macos_update_available": false,
  "actual_pcrd": "0.1.79",
  "web_service_http_status": {
    "5173": 200,
    "4317": 200
  },
  "old_pcrd_followup_remains_paused": true,
  "client_loaded_pid": 30884
}
```
