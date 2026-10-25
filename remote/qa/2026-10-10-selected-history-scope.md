# Selected History Scope

## Scope

- pcrd 0.1.83 replaces whole-state and whole-projection backups with compact,
  read-transaction exports for the selected session and its reachable native
  rollout closure. Unrelated history, SQLite freelists and global WAL bytes
  are no longer multiplied into Agent storage estimates.
- Native table/index/trigger definitions, migration checksums, logical and
  physical identifiers, byte/ordinal cutoffs and raw paginated files remain
  unchanged. Every Agent still receives a separately writable database.
- The same row selectors budget the packed database and create the snapshot.
  Startup storage still includes all candidate and synthesis copies, the shared
  source's peak residency, and each Agent's existing 64 MiB runtime reserve.
  APFS sharing is not used to bypass capacity protection.
- Unknown required database schemas fail closed; integrity and size are
  checked before atomically publishing the private snapshot.
- Native DDL and migration-checksum fingerprints are matched against the
  exercised adapter. Changes to existing tables/triggers and unknown FK
  dependencies are rejected too. Selected raw identities and the history
  closure are revalidated before any Agent can receive the shared baseline.
- A derived private projection with items missing their turn or a watermark
  beyond its full frozen physical file is invalidated as a complete unit.
  Native restores it from unchanged raw bytes; global caches are not edited.
  An orphan is a cache-reuse veto in the supported schema, not a claim about
  every native version's internal invariant.
- General workspace finalization performance work remains paused. No original
  task is resumed, resent, accepted, discarded, or stopped by this repair.

## Screenshot Diagnosis

The screenshot corresponds to run `pcr-20261010T121729Z-ebf6f171` in the memory
decoding workspace. Agent execution completed at 12:32:55 UTC. The later explicit
finalization started at 12:46:04 UTC, not immediately when Agent execution ended.
Measured phases: result preservation 2.72 s, workspace synchronization 6.22 s,
session promotion 20.17 s, and private-workspace cleanup 8.74 s. The finalized
event arrived at 12:46:53 and the worker stopped at 12:46:56 UTC. Thus briefly
waiting for saving after choosing a result is valid; completed Agent execution
is not a durable-save receipt. It was not an indefinitely stuck queue: the
original queued follow-up started as `pcr-20261010T124818Z-7db92b14` and completed
at 12:54:35 UTC. This diagnostic did not resend that message.

## Verification

Test and native acceptance evidence is recorded under
`~/Library/Caches/ParallelCodexMaintenance/HistoryScope-20261010`. Acceptance
uses owned scratch homes only, never submits a model turn, and removes its own
account-support copies. Production activation requires repeated global idle,
message-queue, compatibility, model-catalog, pairing and health checks. Existing
client, Node and Agent processes are not restarted.

- Source: 1063 tests and 153 subtests passed.
- Installed 0.1.83: 1062 tests and 153 subtests passed; the source-location-only
  vendored Textual test was deselected. Installed Python modules match the
  immutable wheel byte-for-byte. The prior 0.1.82 wheel is unchanged.
- Activation gates: 16 tests passed, including active work, pending messages,
  late arrivals and rollback safety.
- Both source and installed native probes prepared five independent homes.
  Full paging agrees exactly with an independent full-state native/raw
  reference, cold projection reconstruction, and a fork adopted into an empty
  home after removal of every candidate, reference and shared baseline:
  9 turns / 728 items, ordered JSON SHA-256
  `1088cd8c78f039dac7f8510faa99c6809ce8ed84b5c515b22aa15ea7378a73b9`.
  Native resume exited before each fresh paging process. No model turn was
  submitted and original raw file hashes stayed unchanged.
- The original global cache had 728 items but only 8 turn rows: 18 items were
  missing their turn header despite an EOF checkpoint. The private-cache
  rebuild removes this inherited paging gap without deleting source history.
- For the originally reported small ECoG workspace and the original 5+3
  settings, installed-code preparation budgeting is now 2,140,281,931 bytes
  (1.9933 GiB), including eight copies, one shared baseline and the existing
  per-Agent reserve. This is not an unlimited task-lifetime disk guarantee.
  The selected state snapshot is 262,144 bytes and the projection 22,867,968
  bytes; the approximately 115 MiB raw history is unchanged.
- Published wheel SHA-256:
  `7b3410fd2828adb58de7445fb1f75f9bd41c66e5e33c3d2e1fa701f191ff3933`.
  Only pcrd is published/installed; macOS and other artifacts are unchanged.
- At 13:31:02 UTC activation correctly returned 75: actual healthy pcrd was
  still 0.1.82 with two active runs. No scheduler or client restart occurred.
  Thus the installed-code result is not yet a production-service result.
- The thread heartbeat `pcrd-0-1-83` checks the audited activation script every
  15 minutes, stays quiet while busy, and must pause itself immediately after
  verifying the actual running version has reached the target. It does not
  restore older paused update or finalization-refactor tasks.

## Scoped Storage Sample

```json
{
  "checked_at_utc": "2026-10-10T13:30:39.824924+00:00",
  "num_agents": 8,
  "workspace_bytes_per_agent": 7888576,
  "workspace_copies_bytes": 63108608,
  "metadata_bytes_per_agent": 238608563,
  "metadata_bytes": 2077173323,
  "total_bytes": 2140281931,
  "shared_source_bytes": 168304819,
  "total_gib": 1.993292878381908,
  "runtime_module": "/Users/mingliangxu/Library/Application Support/parallel-codex-runner/runtime/0.1.83/lib/python3.14/site-packages/parallel_codex_runner_core/app.py",
  "turn_submissions": 0,
  "production_service_using_this_runtime": false
}
```

## Published Backend

```json
{
  "checked_at_utc": "2026-10-10T13:30:44.323438+00:00",
  "pcrd": "0.1.83",
  "wheel_sha256": "7b3410fd2828adb58de7445fb1f75f9bd41c66e5e33c3d2e1fa701f191ff3933",
  "other_artifacts_preserved": true,
  "macos_unchanged": true,
  "scheduler_activated": false,
  "remote_jobs_launched_by_qa": false
}
```

## Activation 2026-10-10T14:06:10.792108+00:00

```json
{
  "actual_version": "0.1.83",
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
  "scheduler_pid_before": 48735,
  "scheduler_pid_after": 44231,
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
  "checked_at_utc": "2026-10-10T14:06:10.792108+00:00"
}
```
