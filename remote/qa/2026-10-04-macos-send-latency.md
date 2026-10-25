# Message Submission Latency: macOS 0.1.406 and pcrd 0.1.69

## Actual Deployment

- macOS **0.1.406 (406)** is published, downloaded, verified, installed and restarted through the built-in updater. Settings visibly report the current version and no newer release. Formal client PID changed from 95889 to 58973.
- pcrd **0.1.68** is still the running scheduler, healthy and paired. Its LaunchAgent SHA-256 remains `517a3d6136b90648689111366acec3e7537e947335f7df6b427ad535e93943e1`; the client update did not restart it.
- pcrd **0.1.69** is built and installed in a separate runtime. It is not yet active. Both `pcr-20261004T010050Z-4b55e870` and `pcr-20261004T011958Z-0459b6f3` remain active at the post-installation health check. Activation must wait for all candidates, synthesis Agents, finalization and other operations to finish.
- Existing Node web-service processes retain their original PIDs/start times. One original Codex launcher (42535) exited because its Agent completed successfully at **09:57:48 CST**, before the updated client started at **09:59:16 CST**. The initial all-Node continuity assertion rejected this natural completion; verification now explicitly records the successful worker status/timing rather than claiming every Node process remained running.
- Android, relay services, research directories and running Agents' private configurations were not modified. No real task was stopped, adopted, retried or resent.

## Diagnosis

The actual accepted continuation command `c9ce859c-6263-4250-8a02-1984d740d9bb-attempt-0-confirmed` entered its handler at **01:00:48.005930 UTC** and finished at **01:00:52.328114 UTC**, taking **4.322184 seconds**. The source was finalized and synchronized before that interval. The earlier approximately 67-second gap waiting for user confirmation is not system submission latency.

The client already distinguishes unconfirmed delivery from a reliable acceptance receipt. Receipt acceptance precedes opening/hydrating the Run. Agent-body requests use `includeDiff=false`; file-change loading is independent and is not a prerequisite for showing an Agent or marking a message accepted. No premature "sent" state was added to conceal backend delay.

A private continuation profile using the same captured **204-Run** state identified avoidable work before the receipt:

- Repeated argument validation loaded model registries four times and parsed the same large, unchanged global TOML configuration four times.
- Two necessary state commits repeatedly encoded the full roughly 9 MiB registry using iterative indented JSON output. Locking, state reloads, flush/fsync and atomic replacement remain necessary; the redundant encoding cost is what changed.

Workspace cloning already runs in a detached worker. Shared manager/mutation locks and finalized-Agent diff collection remain possible contention sources, but their causal overlap with this particular 4.322-second interval was not proven. This release does not claim every network or finalization delay has been eliminated.

## Changes

1. Cache only the three immutable Codex defaults, using a bounded 16-entry cache keyed by the resolved path and device/inode/size/mtime/ctime identity. Validate identity on the opened file before/after parsing and recheck the path afterward. Malformed files and read failures are not cached; atomic replacement and concurrent edits invalidate the entry. Live model catalog loading and effort validation still run.
2. Encode registry JSON once with compact structured serialization, then write the resulting string once. Preserve both distinct persistence boundaries, schema version 3, ordering, Unicode round-trip behavior, 0600 permissions, locking, flush/fsync and atomic replacement. No asynchronous save, skipped validation, stale mutable registry cache or early acceptance acknowledgement.
3. Add an acceptance-boundary client regression: hold the actual receipt, retain the original pending command and block duplicate submission; release acceptance while Run opening is still held, show accepted delivery without resending; then open the same Run.
4. Include the previously pending 0.1.406 Agent-switching and scrolling changes. Cached reading no longer waits for continuation-source confirmation. Passive presentation updates coalesce during ordinary trackpad/momentum activity; explicit user navigation remains immediate. Result-only metric updates do not republish the entire conversation model. See the separate Smooth Reading QA record for detailed source/queue/geometry guarantees.

## Controlled Performance

Uninstrumented callback measurements use the same captured 204-Run snapshot and a private fixture with no real Agent launch:

| Path | Samples | Median |
| --- | --- | --- |
| Installed 0.1.68 | 7 | 1.038182 s |
| New path, first cold sample | 1 | 0.545970 s |
| New path, warm | 7 | 0.357684 s |

The warm callback median improves by approximately **66%**. This is not an end-to-end network/UI measurement and is not a claim that the observed live 4.322-second submission now takes 0.358 seconds. Cold behavior is reported separately and is represented by only one sample.

## Verification

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SendLatency-20261004`.

- Python unittest suite: **776 passed**.
- Repository daemon/core pytest suite: **895 passed, 104 subtests passed**.
- Tests against the installed independent 0.1.69 wheel/runtime: **222 passed, 32 subtests passed**.
- Client suite: **348 tests in 44 suites passed**, plus protocol, desktop-state and icon checks.
- Native resource smoke test: packaged math and syntax rendering passed. Strict code-signature verification passed for the release, downloaded candidate and installed client.
- Independent native acceptance uses a separate app bundle/preference domain and `/private/tmp/pcr-send406`, with its own socket, state and outbox. Cached Agent 2 stays visible while source confirmation is held. Held acceptance shows Sending; releasing that receipt while Run opening remains held shows accepted delivery. Later Run opening with body/diff still held does not return to Sending. Exactly one private continuation command uses the confirmed Agent 2 source.
- Signature-stripped native executable code exactly matches both the released 0.1.406 executable and the earlier native Agent-switch/scroll acceptance build. Native QA clients and fixture services are stopped.
- The native harness needed corrections for a turn-index schema value and registry fixture pointer. Those are fixture corrections, not product bug claims. Artificially held diff responses can time out.
- Build uses the supported macOS 26.5 SDK. The default 27 SDK attempt failed because SwiftUI macro support was unavailable; no toolchain settings were changed globally. An initial incorrectly named resource-smoke option started an auxiliary built app; only that auxiliary PID was terminated. The corrected smoke invocation passed, without real lifecycle requests.

Release executable SHA-256: `8f18c64e48f1ac2a5ded11e14e09389b73f0d06ad65e83ddbd535095338e8074`.
0.1.69 wheel SHA-256: `ac4c58a6c7aea300c1b9dba84de4b8cf9dc421513cbc05ab2995081a0c352afd`.

Files: `submission-benchmark.json`, `native-acceptance.json`, `client-update-prepared.json`, `client-installation.json`, `formal-updated.png`, `relay-publish-406.json`, `staged-runtime.json`, test/build logs and `partner-review.txt`/`partner-review.png`.

## Deferred Scheduler Activation

Use the 0.1.69 runtime to run `SendLatency-20261004/activate_pcrd.py`. It verifies the build/tests/native acceptance/actual client installation/wheel, checks all active runs and operations repeatedly, requires detached workers, and performs a read-only GPT-6.1 Sol catalog preflight. Active work or an unavailable catalog returns 75 without restarting. Versions already at 0.1.69 or newer are not downgraded or restarted. Failure after a switch restores the previous LaunchAgent and reports the actual version and health. The script records actual successful activation in this document; until that record exists, the new backend submission optimization is **not live**.

The post-installation activation attempt returned **75**, with both current runs active, no active operation and actual version **0.1.68**; `restarted=false`. The existing paused deployment heartbeat was updated, rather than duplicated, to "activate pcrd 0.1.69 after tasks finish" and is active. It remains quiet for unchanged/non-actionable state and reports verified activation or a concrete failure. No scheduler restart occurred this turn.

## Scheduler Activation 0.1.69

```json
{
  "pcrd_version": "0.1.69",
  "healthy": true,
  "active_runs_before": [],
  "active_runs_after": [],
  "visual_file_comparison": true,
  "gpt_6_1_sol_available": true,
  "controller_paired": true,
  "existing_node_services_preserved": true,
  "node_processes_exited_during_verification": [],
  "restarted_only_scheduler": true
}
```
