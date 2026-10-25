# Startup Performance: pcrd 0.1.77 / macOS 0.1.417

## Scope And Root Cause

The previous preparation path made isolation unnecessarily expensive: each
candidate backed up the state database, parsed and rebound the complete history,
and changed native paginated history into legacy history. Native Codex then lost
the existing projection/recovery path. Optional model warmup also occupied the
two preparation slots while waiting on a cache lock or a subprocess. Finally,
the previous `agent_started` event preceded process creation and real turn
acceptance, so the UI reported execution during preparation.

This release changes the preparation and storage boundaries, not a timeout or
the wording of an error. It does not truncate history, share writable databases,
raise parallelism, or replay any user task.

## Implementation

- New conversations initialize a clean private state; they do not copy the global
  session database when there is no history to resume.
- A batch owns one immutable source generation and legal SQLite backups of the
  state database and native history projection. Preparation overlaps the existing
  workspace preparation. Ready candidates start without waiting for all candidates.
- Each candidate receives independent writable APFS clones, with ordinary independent
  copies as the portable fallback. Hard links and shared writable state are forbidden.
- Paginated history keeps its original bytes, complete ancestor closure, canonical
  filenames, physical rollout identities, ordinals, byte cuts, and native projection.
  The selected database path is authoritative, including after revert. No downgrade
  to legacy history occurs on this production path.
- Legacy compatibility history is parsed once per source and separately rebound
  only at indexed execution-context records. Retained-fingerprint retries continue
  to reuse their original private environment rather than creating a new one.
- Current execution `cwd` and `runtimeWorkspaceRoots` are bound at the native
  request boundary. Historical text is not rewritten to simulate isolation. Existing
  explicit user permissions and the production full-access policy are unchanged;
  this is not a new OS filesystem sandbox.
- Optional warmup only consumes an already-ready compatible cache. A busy lock,
  unknown CLI version, or absent snapshot means immediate skip, not a hidden wait.
  No version subprocess or asynchronous mutation of an executing private home is added.
- Adoption imports the complete native history closure and the selected native
  projection into the destination. Cleanup retains the native history database and
  archived sessions. Post-execution sanitization processes only newly appended lines.
- Process creation, history restore, submission, accepted turn, first new activity,
  and cleanup are separate factual events. Historical replay is not new work.
  Discovery, baseline preparation, and previous-result finalization have separate clocks.
- macOS displays the real stages and a collapsed timing section. Timing-only polling
  updates do not invalidate the conversation reader or add transcript rows/extra polling.

## Installed Runtime Measurements

Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/StartupPerformance-20261006/installed-native-probe.json`.

The actual installed 0.1.77 wheel, using Codex CLI 0.159.2, was exercised on an
owned temporary copy of the real long source. No user turn was submitted.

| Measurement | Observed |
| --- | ---: |
| Complete source history | 1,977,264,514 bytes (about 1.84 GiB) |
| State database | 307,310,592 bytes |
| Native history projection | 5,686,185,984 bytes |
| Five private environments, two existing preparation slots | 8.631 s total |
| PCR complete-history JSON records parsed on paginated path | 0 |
| Private materialization per candidate after common dependency | 0.555-0.596 s |
| Two native resume samples | 1.714 s / 5.633 s |
| Sampled native subprocess RSS | about 49 MiB |
| Native fork (no model turn) | 0.470 s |
| Adopted fork resume after deleting all candidate/shared homes | 5.266 s |

The source hash/stat remained unchanged. Independent database writes were verified.
Native responses reported the current QA workspace as `cwd` and as the sole runtime
workspace root while other candidate workspaces still existed. Adopted storage
remained independently recoverable after deleting the candidate/source templates.

These are preparation and native storage-recovery measurements, not five-agent
end-to-end latency, a speedup ratio, whole-pipeline peak RSS, or a verified user
model response. Native catalog/network retries and workspace baseline/finalization
can still take time; those are not claimed to be eliminated by file cloning.

## Verification

- Source suite: 998 passed, 120 subtests passed.
- Installed 0.1.77 runtime suite: 997 passed, 1 repository-only test deselected,
  120 subtests passed. Runtime production module bytes match the immutable wheel.
- macOS: 340 tests in 39 suites passed, headless/offscreen only. Interactive focus
  tests were intentionally not run. Production build, signature, packaged math/syntax
  resource smoke, and native icon rendering checks passed with macOS SDK 26.5.
- The actual 417 generated Swift decoder successfully decoded nine snapshots
  produced by the installed 76 and 77 Python runtimes, including all three new
  phase values. Five deployment-gate tests proved that an old client or a replaced
  bundle with its old process still running cannot trigger the scheduler switch.
- A real owned subprocess fixture remained silent in resume for 91 seconds,
  completed successfully, and submitted exactly one fixture turn. No `agent_started`
  event occurred until actual acceptance. Explicit fixture cancellation completed in
  0.817 seconds with zero submissions. No user task was stopped or resubmitted.
- Regression coverage includes canonical physical IDs after revert, missing selected
  history refusing an older fallback, native closure adoption/projection isolation,
  source preparation lifecycle cleanup, ready-only cache behavior, phase ownership,
  finalization telemetry failure isolation, and 200 metric-only updates causing no
  reader publication.
- During verification, a noncanonical imported filename was caught by real native
  recovery and fixed before the wheel was built. A package-version metadata mismatch
  was fixed and both full Python suites rerun. No failed check is represented as passing.

## Release And Activation Boundary

The target pair is macOS 0.1.417 and pcrd 0.1.77. The new phase values are additive
in the server protocol, but macOS 0.1.416 uses a closed Codable candidate-status
enum. Activating 0.1.77 under that old reader can break snapshot decoding. The
activation gate therefore requires an actually relaunched macOS build 417 or newer
as well as repeated global idle, detached workers, pairing, and a readable GPT-6.1
Sol model catalog. Publication does not itself restart or install the formal client.

At pre-publication verification, the formal client was 0.1.416 and the scheduler
0.1.76, healthy and paired. Global active runs/operations and prompt delivery were
empty. No QA windows, client restart, Android operation, research-directory write,
or Node/service stop was performed. Final publication and activation results are
appended below by the audited scripts.

## Native Contracts Consulted

- [Native history context recovery](https://raw.githubusercontent.com/openai/codex/rust-v0.159.2/codex-rs/thread-store/src/local/model_context.rs)
- [Native rollout resolution](https://raw.githubusercontent.com/openai/codex/rust-v0.159.2/codex-rs/thread-store/src/local/thread_rollout_resolver.rs)
- [Native history materialization](https://raw.githubusercontent.com/openai/codex/rust-v0.159.2/codex-rs/thread-store/src/local/thread_history_materialization.rs)
- [Codex app-server lifecycle](https://developers.openai.com/codex/app-server/)

## Update Availability And Deferred Follow-Up

The paired controller's actual macOS update check returned 0.1.417 and the same
archive size/hash as the signed published artifact. No download, installation,
client restart, or window activation was performed. The activation script was
then exercised with the actual 0.1.416 client: it returned 75 with
`compatible_client_not_running`, leaving the healthy 0.1.76 scheduler unchanged.

Thread heartbeat `pcrd-0-1-77`, named
“客户端升级且任务结束后启用 pcrd 0.1.77”, is active. It may only switch the scheduler
after the actual client has been upgraded/relaunched and every task, finalization,
admission and delivery is idle. It does not itself install/restart the client.
Unchanged/non-actionable checks remain quiet. Successful activation or a concrete
failure is reported and recorded separately; this publication is not represented
as an already-active performance fix on the old formal installation.

Build 417 remains the minimum supported macOS companion after activation, including
later reconnects. Running an older copied client is not validated or supported for
the new stage values; a backend binary rollback is not a promise that an old
client can consume every already-persisted new protocol value. This release gate
checks the local formal client, not a newly implemented per-connection capability
negotiation system. No such broader protocol compatibility claim is made here.

## Verified Publication

```json
{
  "published_at_utc": "2026-10-05T19:39:48.811879+00:00",
  "macos_published": "0.1.417",
  "macos_archive_sha256": "d8e49c2c152deef095f5aa34e4f8901d0c9abdb704cbc99a332ad6b75b567636",
  "pcrd_release_version": "0.1.77",
  "pcrd_wheel_sha256": "212a55dd2af6737606160e3c3adea73c0aab7d91eb91e7e662ea50868607a09d",
  "actual_client_version": [
    "0.1.416",
    "416"
  ],
  "actual_scheduler_version": "0.1.76",
  "baseline_client_and_node_processes_preserved": true,
  "formal_client_installed_or_restarted": false,
  "scheduler_activated": false,
  "qa_windows_opened": false,
  "user_tasks_stopped_or_resubmitted": false
}
```

## Deferred Activation: Client Compatibility

```json
{
  "checked_at_utc": "2026-10-05T19:40:31.024087+00:00",
  "deferred": true,
  "reason": "compatible_client_not_running",
  "required_macos_build": 417,
  "actual_client_version": "0.1.416",
  "actual_client_build": "416",
  "old_client_process_retained": true,
  "actual_version": "0.1.76",
  "scheduler_restarted": false,
  "client_installed_or_restarted": false,
  "qa_windows_opened": false
}
```

## Activation 2026-10-06T00:12:59.499937+00:00

```json
{
  "actual_version": "0.1.77",
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
  "scheduler_pid_before": 23860,
  "scheduler_pid_after": 81753,
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
  "checked_at_utc": "2026-10-06T00:12:59.499937+00:00"
}
```
