# macOS Multi-Workspace Refresh

## Confirmed Causes

The production 0.1.428 client used `Task.detached(.userInitiated)` for synchronous
Unix socket connect/write/read and JSON decoding. Utility preloads were promoted
to the same executor. A five-second process sample showed more than ten Swift
cooperative threads waiting in `Darwin.read -> sendUnixRequest`, while the main
thread mostly waited in its run loop. This is executor starvation, not evidence
of a continuously CPU-saturated main thread. Conversation cache reads, writes,
and locks also occupied cooperative threads.

Active, non-visible conversations were preloaded again every 15 seconds:
history from cursor zero, followed by up to four Agents' pages. Independent
lightweight background run polling already existed. Repeated warming therefore
duplicated work instead of keeping the visible reader current.

A separate reproducible correctness bug allowed a late background preload to
replace a newer foreground cache, including its selected Agent, with an earlier
snapshot. That could restore old content on subsequent navigation.

## Repair

Move synchronous transport work to Dispatch through checked continuations.
Background observation has a shared, four-request OperationQueue budget; pending
Swift tasks suspend, and foreground reads have a separate Dispatch path.
Only known read-only actions use that budget; automatic commands remain eligible
even at utility priority. Relay requests use the inner action, not the wrapper.
This does not limit Agent execution or preparation. Cache I/O uses a separate
serial Dispatch queue. Preserve the original RPC payload, command ID, retry,
authentication, timeout and in-flight result collection semantics.

Separate successful first warming from ongoing status observation. An active
run warms once in the background; known terminal runs are warmed independently,
with failed attempts retaining retry eligibility. Foreground readers continue
incremental work/answer reads on navigation, including newly created synthesis
Agents. No history is truncated or discarded.

Reject preload results after navigation to that workspace, a changed run pointer,
or a newer same-key cache snapshot. Keep metadata event counts monotonic. A cache
read or mutation must not synchronously hold a cooperative executor thread.

## Evidence Boundary

Old-code native regressions failed all three tests, with eight expectations:
foreground answer reading waited about 2.10 seconds until background release;
full-history warming repeated; and the newer foreground cache was overwritten.
The repaired constrained-pool regressions pass, including a non-visible native
NSHostingView that changes before the 24 pending background reads are released.
Its answer RPC/acceptance takes about 0.001 seconds in this controlled fixture;
this is not a promised production latency or a GUI frame-time measurement.
Cancellation also retains exactly one original request, and disk rehydration
preserves the newer foreground answer and Agent selection.
Additional regressions cover local and relay commands bypassing saturated reads,
relay read budgeting, metadata ticks during an in-flight preload, and a terminal
transition before active warming finishes. Metadata advances do not invalidate
the conversation snapshot or consume the later terminal warming.

Real, bounded, read-only probing found metadata around 0.24-0.34 seconds and first
work-event pages around 1.26-9.11 seconds. In one overlap check, an Agent-8 event
page took 11.55 seconds; same-run metadata returned in 1.12 seconds while that
page was still pending. The repair does not claim to eliminate backend file
scanning, remote computation, or every service-side delay.

Formal-client observation also showed that the selected completed Agent-1 reply
was already visible while synthesis Agents continued running. A completed
selected branch legitimately has no new output; do not auto-switch, adopt a
different Agent, or resubmit any user request to manufacture visible progress.

Tests, signing, archive publication, preparation, actual installation and runtime
acceptance are separate gates. Verified results will be appended below. This is
a macOS-only release; pcrd, Agent workers, Node services, research directories,
drafts, user queues and result-adoption state must remain unchanged.

Independent design discussion: https://chatgpt.com/c/6ac94970-9e04-83e8-b6cc-4402e888e333

## Source Validation

- SDK 26.5, native SwiftPM, one build job, nice 15; interactive QA disabled.
- Six strict cooperative-pool regressions passed. Local and relay background
  reads share the bounded budget, but local and relay commands do not.
- All 439 native client tests in 51 suites passed, including existing typing,
  viewport stability, account usage, answer delivery, outbox and queue tests.
- All 65 Python protocol, usage, plugin and release tests plus seven subtests
  passed. Generated conversation protocol checked without regeneration.
- Fixture cancellation collects one original response; no fixture submits a
  user turn or uses the production socket. The native render stays offscreen.
- Final signed release build and actual deployment are separate later gates.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T20:42:23.680696+00:00",
  "macos": "0.1.429",
  "archive_sha256": "6df0837e13d119e5bd410ab9f1f5e49446cc07d0fc72ebe2c747d8d68824cdc4",
  "relay_catalog_verified": true,
  "actual_installed_client": [
    "0.1.428",
    "428"
  ],
  "client_tests": 439,
  "client_suites": 51,
  "python_contract_tests": 65,
  "blocked_background_reads": 24,
  "strict_cooperative_pool_regressions": 6,
  "test_sdk": "MacOSX26.5.sdk",
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T20:42:37.991720+00:00",
  "macos_prepared": "0.1.429",
  "actual_installed_client": [
    "0.1.428",
    "428"
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
  "checked_at_utc": "2026-10-09T20:43:29.666219+00:00",
  "macos": "0.1.429",
  "build": "429",
  "client_pid": 2035,
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
  "authoritative_server_queues_unchanged": true,
  "empty_server_queue_cache_refreshed": false,
  "active_runs_preserved": [
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

## Installed Client Acceptance

The formal 0.1.429 process was sampled for five seconds after installation.
All sampled `sendUnixRequest` stacks were rooted in the dedicated observation
OperationQueue or independent Dispatch queues, not Swift cooperative workers.
Conversation cache work appeared on its serial Dispatch queue. The main thread
continued its run loop with intermittent layout work; this sample is not an
input-latency benchmark or a claim that all layout cost is gone.

Read-only native accessibility observation confirmed that the original
workspace, Agent 1 selection, completed reply, draft and bottom reading position
were restored. Synthesis Agents 6-8 advanced their recent-output time from 04:43
to 04:45 and their reasoning counts continued increasing. No selection, task
submission, result adoption or research-file change was used for acceptance.
The completed selected branch correctly retained its completed text.

Evidence: `WorkspaceRefresh-20261010/client-before.sample`, `client-after.sample`,
`before-ui-install.json`, `before-result-choices.json`,
`Verified Actual Installation.json`, strict and complete native test logs.
