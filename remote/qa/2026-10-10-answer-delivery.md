# Terminal Candidate Answer Delivery

## Scope

macOS 0.1.427, based on 0.1.426. No scheduler, worker, research workspace,
queue business intent or Android change. Automated validation is windowless
or offscreen; no foreground QA windows or global keyboard/mouse takeover.
Formal installation uses the existing client's built-in update action,
followed by read-only UI acceptance.

## Observed Cause

The screenshot matches the saved Agent 5 branch of
`pcr-20261009T145235Z-e93791e3`. Its warm cache contained six opening/work
blocks, cursor 205, empty final text and `finalMessageHasMore=false`, but no
evidence that the answer had been read after this candidate completed.
The live scheduler reports this candidate successful and returns its complete
3271-byte saved answer. SHA256 of the returned UTF-8 content is
`ca46d7caf4b21dd39a5a8bec6168888256d894e9ae18c5a49b99448efde26626`.
The protocol's artifact message ID is a version identity, not a content hash.

The old selection path treated any readable work cache as already loaded and
only scheduled an answer tail when its old `hasMore` flag was true. Summary
polling scheduled only a work/detail reader. A running-era empty answer EOF
could therefore suppress a terminal answer read; a held work request could
also delay it. Installed 423 and published 426 have identical critical loader
files, so simply installing the previously published UI releases was not a
repair for this mechanism. Cache write time does not prove an RPC was issued
or identify an unobserved server stall.

## Repair

- Keep first-paint readability separate from terminal answer confirmation.
- Persist an optional answer-read status captured before its request. Legacy
  caches remain decodable but cannot invent a terminal receipt.
- Independently fetch and paginate the selected terminal answer on warm
  selection, summary transition and cache restoration, with selection-scoped
  deduplication separate from work requests.
- Merge all answer writers through one acceptance rule. Late running/empty
  receipts cannot erase completed text or truncate an unread answer tail.
- Failed answer reads preserve output and execution status; polling retries
  with bounded backoff, while manual refresh can immediately retry. A valid
  terminal empty answer is confirmed once instead of polling forever.
- Manual work reconstruction preserves independently fetched final text.

## Evidence

Local evidence directory:
`~/Library/Caches/ParallelCodexMaintenance/AnswerDelivery-20261010`.
The two root regression scenarios failed on the unchanged loader (exit 1)
and pass with the repair. Earlier incorrect-toolchain attempts were compile
failures, not behavioral evidence. The scene replay uses the real saved cache
and read-only answer receipt against an isolated transport, holds work reads,
checks all UTF-8 bytes and renders the recovered final answer offscreen.
No real user turn is submitted, repeated, adopted, discarded or stopped.

Release, signing, resource checks and actual activation evidence are appended
below only after each corresponding operation succeeds.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T19:29:09.985730+00:00",
  "macos": "0.1.427",
  "archive_sha256": "dc03bf6ac1ab9291006746b8c93d7b40c4f18b53f4fc26567cb9015638de4567",
  "relay_catalog_verified": true,
  "actual_installed_client": [
    "0.1.423",
    "423"
  ],
  "client_tests": 430,
  "client_suites": 50,
  "python_contract_tests": 97,
  "answer_delivery_regressions": 11,
  "real_cache_replay_bytes": 3271,
  "test_sdk": "MacOSX26.5.sdk",
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T19:29:55.656830+00:00",
  "macos_prepared": "0.1.427",
  "actual_installed_client": [
    "0.1.423",
    "423"
  ],
  "actual_pcrd": "0.1.80",
  "scheduler_pid": 46777,
  "client_not_restarted": true,
  "builtin_integrity_and_signature_verifier_used": true,
  "healthy": true,
  "paired": true,
  "no_qa_window_or_user_input": true
}
```

## Verified Actual Installation

```json
{
  "checked_at_utc": "2026-10-09T19:31:52.937641+00:00",
  "macos": "0.1.427",
  "build": "427",
  "client_pid": 81628,
  "loaded_executable_inode_and_hash_verified": true,
  "builtin_update_lifecycle_used": true,
  "foreground_app_was_already_pcr_before_update": true,
  "background_relaunch_claimed": false,
  "ordinary_quit_not_used": true,
  "actual_pcrd": "0.1.80",
  "scheduler_pid_unchanged": 46777,
  "worker_and_node_pids_and_start_times_preserved": {
    "33933": "Sat Oct 10 00:58:34 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "33987": "Sat Oct 10 00:59:08 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "40479": "Sat Oct 10 01:22:35 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "46777": "Fri Oct  9 16:04:19 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "68341": "Fri Oct  9 18:00:16 2026     /opt/homebrew/Cellar/node/26.0.0/bin/node"
  },
  "outbox_and_followup_queue_preserved": true,
  "persisted_drafts_preserved": true,
  "active_runs_preserved": [
    "pcr-20261009T165829Z-72e0a5ca",
    "pcr-20261009T165903Z-b06ff0c7",
    "pcr-20261009T172232Z-d47fdb49"
  ],
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_http_status": 200,
  "rollback_performed": false,
  "no_user_turn_submitted_or_result_adopted": true
}
```

## Verified Formal Client Answer Recovery

```json
{
  "checked_at_utc": "2026-10-09T19:33:06.666734+00:00",
  "run_id": "pcr-20261009T145235Z-e93791e3",
  "agent": 5,
  "answer_bytes": 3271,
  "answer_sha256": "ca46d7caf4b21dd39a5a8bec6168888256d894e9ae18c5a49b99448efde26626",
  "terminal_read_receipt": "success",
  "answer_tail_complete": true,
  "cache_written_by_formal_client": true,
  "cache_not_manually_repaired": true,
  "real_user_turn_not_replayed": true
}
```

## Formal UI Acceptance

The existing formal client was already the foreground app before installation.
Its Settings update action identified 0.1.427 explicitly, then used the
`isUpdating` termination path, not ordinary quit's implicit-adoption path.
The new executable's mapped inode and SHA256 match the tested package.
This was not an `activateOnRelaunch=false` background relaunch; no different
foreground app was taken over, and no QA application/window was launched.

Read-only workspace navigation and a saved-conversation text search opened
the original historical run and selected Agent 5. The formal native UI showed
its exact opening line, the five original tools, the complete final answer,
and its closing statement that the question does not initiate training.
All eight candidates, including Agent 4,
showed terminal states; Agent 5 was completed/read-only. The formal client,
not a maintenance script, wrote the recovered 3271-byte answer and successful
terminal read receipt to its original branch cache. The full UTF-8 SHA256
matches the read-only server answer, not just its first page.

After acceptance, search was closed and the original fine-hand workspace and
Agent 1 selection were restored. Its original unsent draft remained visible.
Scheduler, all three existing worker processes and the Node server retained
their PIDs and start times. Both pending outbox messages and the daemon
follow-up queue were unchanged. No real turn was replayed, accepted, stopped,
discarded or submitted by the maintenance operation.

## Verified Formal Client Answer Recovery

```json
{
  "checked_at_utc": "2026-10-09T19:34:38.086200+00:00",
  "run_id": "pcr-20261009T145235Z-e93791e3",
  "agent": 5,
  "answer_bytes": 3271,
  "answer_sha256": "ca46d7caf4b21dd39a5a8bec6168888256d894e9ae18c5a49b99448efde26626",
  "terminal_read_receipt": "success",
  "answer_tail_complete": true,
  "cache_written_by_formal_client": true,
  "cache_not_manually_repaired": true,
  "real_user_turn_not_replayed": true
}
```

## Verified Actual Installation

```json
{
  "checked_at_utc": "2026-10-09T19:34:44.324087+00:00",
  "macos": "0.1.427",
  "build": "427",
  "client_pid": 81628,
  "loaded_executable_inode_and_hash_verified": true,
  "builtin_update_lifecycle_used": true,
  "foreground_app_was_already_pcr_before_update": true,
  "background_relaunch_claimed": false,
  "ordinary_quit_not_used": true,
  "actual_pcrd": "0.1.80",
  "scheduler_pid_unchanged": 46777,
  "worker_and_node_pids_and_start_times_preserved": {
    "33933": "Sat Oct 10 00:58:34 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "33987": "Sat Oct 10 00:59:08 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "40479": "Sat Oct 10 01:22:35 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "46777": "Fri Oct  9 16:04:19 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "68341": "Fri Oct  9 18:00:16 2026     /opt/homebrew/Cellar/node/26.0.0/bin/node"
  },
  "outbox_and_followup_queue_preserved": true,
  "persisted_drafts_preserved": true,
  "active_runs_preserved": [
    "pcr-20261009T165829Z-72e0a5ca",
    "pcr-20261009T165903Z-b06ff0c7",
    "pcr-20261009T172232Z-d47fdb49"
  ],
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_http_status": 200,
  "rollback_performed": false,
  "no_user_turn_submitted_or_result_adopted": true
}
```
