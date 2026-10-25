# Canonical Rollout Filename

## Root Cause

The failed source-mode run `pcr-20261012T034220Z-3ce81ea4` stopped during
`thread/resume`, before submitting a model turn. Its selected history is an
old PCR import named `rollout-<session UUID>.jsonl`, now stored in native
paginated format. The shared preparation copied that basename unchanged.
Native 0.159.2 requires the creation timestamp and thread/physical identities
in the filename, even when SQLite already points to the correct file.

## Fix Scope

- Preserve existing native filenames, including distinct physical IDs after revert.
- Normalize only recognized short imports with matching metadata identity and
  a valid original timestamp, in new private copies or explicitly adopted copies.
- Generate canonical filenames for new legacy templates, preventing recurrence
  when native later migrates them to paginated format.
- Keep paginated raw bytes, ordinals, lineage cutoffs, global history, and
  independent Agent databases unchanged. Reject ambiguous identities.
- Do not resubmit the failed user request or alter pinned workers.

## Evidence

Native naming contract inspected in the matching upstream tag:
https://github.com/openai/codex/blob/rust-v0.159.2/codex-rs/rollout/src/rollout_file_name.rs

Source, installed artifact, native acceptance and actual activation are recorded
separately below. No deployment is claimed until its activation record exists.

## Validation

- Source: 1080 tests and 153 subtests passed.
- Installed wheel: 1079 tests and 153 subtests passed; the single source-only
  vendored Textual layout test was deselected, not a runtime behavior test.
- Activation gates: 16 tests passed, including busy queues, late arrivals,
  already-upgraded services, missing models and idle-only rollback.
- Both source and installed native probes first reproduced the exact filename
  rejection in an owned copy of the affected 1,980,143,292-byte history.
- An independent reference used a full source SQLite backup and a manually
  constructed native filename, bypassing the new exporter. All 93 turns and
  15,338 items matched in order and content across hot/cold projection reads,
  fresh processes, native fork, and adoption after deleting the other QA copies.
  Content SHA-256: `d04ef7e5e2adc700e9437392382db530de66eba9aa8c1e8eb2afa37b770f6c18`.
- Original history SHA-256 stayed
  `8a9bafd83b8d7ebb976ea8695fcf975868b1804f075160744cd9d82d607e911b`.
  No model turn was submitted. Scratch authentication/history copies were removed.
- Preparation completes before `run_one_agent`; preparation exceptions return
  an error without starting native execution. The paginated adoption path copies
  its self-contained closure before publishing its thread row. No new claim is
  made about transactionality of unrelated legacy finalization mechanisms.
- New legacy templates and old short imports use the same naming helper. Native
  revert suffixes are preserved, ambiguous identities fail closed, and converting
  an old legacy name never overwrites an existing canonical destination.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/RolloutFilename-20261012`.
Only `app.py` and `resume_history.py` differ from the previous immutable runtime;
the release harness verifies every installed module against the wheel and the
remaining app definitions against 0.1.83. Existing pinned workers are untouched.

## Published Backend

```json
{
  "checked_at_utc": "2026-10-12T04:01:02.690213+00:00",
  "pcrd": "0.1.84",
  "wheel_sha256": "06616f1565b517c9695e0aa68c927e2eb45acf82b80be0cf3aa0270a7b523be9",
  "other_artifacts_preserved": true,
  "macos_unchanged": true,
  "scheduler_activated": false,
  "remote_jobs_launched_by_qa": false
}
```

## Activation 2026-10-12T04:01:30.604434+00:00

```json
{
  "actual_version": "0.1.84",
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
  "scheduler_pid_before": 44231,
  "scheduler_pid_after": 4030,
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
  "checked_at_utc": "2026-10-12T04:01:30.604434+00:00"
}
```
