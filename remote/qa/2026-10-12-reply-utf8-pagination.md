# Reply UTF-8 pagination

## Live reproduction

- Running pcrd 0.1.84 returned the exact reported error for read-only `runs.agent`,
  run `pcr-20261010T175546Z-91f0dd31`, Agent 5, final-message cursor 1980.
- The saved artifact is 1980 bytes and strictly valid UTF-8. Its SHA-256 is
  `3db27ec53131fb27b97d027cceeed5985c188968c81dc01bd7da312681232a13`.
- The existing macOS cache holds a complete 1980-byte answer and an earlier
  rendered message identity. After adoption, workspace-link rewriting produces
  2016 visible bytes. Byte 1980 is now the continuation byte `0xbc`.
- A read-only request with cursor 0 succeeds. Visible answer SHA-256:
  `c45c99410812a60be1107a297ca3e024f02455e47007235252bd8fc2a5aa3a11`.
- The old reader checked only page ends, then decoded a slice beginning inside
  a character before the client could observe the changed message identity.
  This is a stale rendered-byte cursor, not a corrupt answer or failed Agent.

## Fix boundary

Restart from byte zero when a stale cursor splits a UTF-8 code point, in both
raw and transformed readers. Preserve message identity, strict source decoding,
byte limits, and existing client generation checks. Do not round forward,
discard bytes, use replacement decoding, edit saved answers, or replay tasks.
The existing macOS merge resets on changed identity before appending byte-zero
pages; aligned stale cursors already trigger a separate byte-zero read.

## Acceptance

- Source: 1085 tests and 203 subtests passed.
- Independent installed 0.1.85 runtime: 1084 tests and 203 subtests passed;
  the source-layout-only vendored Textual test is excluded as in earlier releases.
- macOS background tests: 440 tests in 51 suites passed, including the added
  stale-EOF merge/late-response/repeated-EOF regression. Interactive QA was off.
  The initial ad-hoc Swift invocation used the incompatible default SDK;
  acceptance uses the existing test script with MacOSX26.5 SDK and CLT Testing
  framework paths. No production client source was changed.
- Both source and installed reader returned the complete 2016-byte real answer
  from stale cursor 1980, exactly matching the live cursor-zero reference;
  the original artifact SHA remained unchanged. No user turn was submitted.
- Activation guard tests: 16 passed. Source and installed module bytes verified.
  Compared with the immutable 0.1.84 wheel, only the two paging functions changed.
- 0.1.85 wheel SHA-256:
  `cb5c1722cdbf86c2e780dce068723e18887ae16d8fd9ee80e30a615dbe994f8e`.
- Evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ReplyPaging-20261012`.

Release and installation are distinct from activation. The scheduler switches
only after all existing runs, operations, admission/delivery and followup queues
are idle. No macOS client restart is required.

## Live acceptance

At 2026-10-12 07:46:21 UTC, all local/global runs, operations and queues were
confirmed empty repeatedly. The guarded activation enabled pcrd 0.1.85,
healthy, paired and connected, with GPT-6.1 Sol available. Only the scheduler
interpreter changed. The existing client binary and client/Node process
identities were preserved; no user tasks were stopped or resubmitted.

The same production `runs.agent` request with stale cursor 1980 now returns
cursor 0 and the exact complete 2016-byte answer, matching the independent
cursor-zero reference. The source artifact remains byte-identical.

The already-running macOS client recovered through normal refresh without
restart, UI clicks, cache edits or message replay. AX confirms the refresh-error
banner is absent and the adopted answer remains visible. Its persisted Agent 5
cache now contains all 2016 bytes, `finalMessageHasMore=false`, cursor 2016,
and message identity `07272db20f8222721e457fb18ca0850b769860beac7b5495d83824fa76be1bc4`.
No deferred-update automation was necessary.

## Published Backend

```json
{
  "checked_at_utc": "2026-10-12T07:45:01.682429+00:00",
  "pcrd": "0.1.85",
  "wheel_sha256": "cb5c1722cdbf86c2e780dce068723e18887ae16d8fd9ee80e30a615dbe994f8e",
  "other_artifacts_preserved": true,
  "macos_unchanged": true,
  "scheduler_activated": false,
  "remote_jobs_launched_by_qa": false
}
```

## Activation 2026-10-12T07:46:21.451453+00:00

```json
{
  "actual_version": "0.1.85",
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
  "scheduler_pid_before": 4030,
  "scheduler_pid_after": 65368,
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
  "checked_at_utc": "2026-10-12T07:46:21.451453+00:00"
}
```
