# Retained Retry and Android Reader Fixes, 2026-10-31

## Root Cause

- Ordinary retry and maintenance recovery did not have identical dispatch rules.
  Candidate preparation could archive metadata and recopy a workspace; targeted
  synthesis retry could regenerate its context. Having native resume support in
  the maintenance entry point did not make these ordinary retry paths equivalent.
- Retained dispatch rejected an existing idle worker even though its request loop
  already understood retained fingerprints. Retry availability could advertise a
  path that could not actually dispatch.
- Android deleted the cached transcript after retry, then waited for history.
  Completed-Agent selection waited for continuation-source confirmation before
  changing the reader. A later queue response could also overwrite that selection.

## Changes

- `plugin_runtime.py`: candidate and synthesis `runs.retry_agent` use the original
  execution receipt, private Codex history, thread, command and workspace. Missing
  or inconsistent evidence returns an explicit error without starting another task.
  Retry does not automatically refresh other completed synthesis results.
- `app.py`: candidate retry preparation and targeted synthesis retry retain their
  execution. Known configuration failure before input acceptance remains a separate,
  fingerprint-checked path. User-stopped Agents are not revived by ordinary retry.
- `plugin/lifecycle.py`, `plugin/worker.py`: live candidate channels advertise
  retained-retry support. An older busy worker without that capability is not fed
  an ambiguous retry command. It must reach idle; its existing idle request loop
  can then resume the retained execution. Synthesis is not sent into a candidate
  channel. No running worker was replaced to remove this compatibility boundary.
- Android `RunControlActions.kt`, `RunMutationFlow.kt`, `FollowUpQueueActions.kt`:
  preserve body and cursor on retry; release the mutation fence before history IO;
  show the selected Agent immediately and read concurrently with source confirmation;
  queue responses no longer choose the reading target.

## Verification

- Full Python suite: **1348 passed, 367 subtests passed**, 118.58 seconds.
- Full retry chain: real local RPC client/server, controller, manager, persistent
  worker subprocess, request files and native adapter. Only the native JSON-RPC
  peer is a deterministic fixture. Both roles retain thread/workspace, send one
  continuation rather than replay the original task, preserve sibling results and
  synthesis context, and replay the same RPC command ID without another dispatch.
- The installed 0.1.134 wheel passed three focused chain/capability tests. Two
  ordinary retry chains also passed with the actual installed 0.1.133 idle worker.
- Android: **294 JVM tests**, release lint and signed release build passed.
  **21 emulator tests passed**, including four new real ViewModel/repository/Compose
  tests with delayed or failed outer transport responses. Cached and uncached
  selection, retry body retention, rejected retry and stale queue responses are covered.
- Four separate persistent-login integration tests require a local relay on port
  18174. The initial unrestricted emulator run failed those four with connection
  refused; that fixture was absent. They were excluded from the subsequent 21-test
  run, not changed or counted as passing. No production authentication was altered.
- Signed, minified release APK installed and opened on the isolated Android 15
  emulator. Physical-phone installation and authenticated real-phone UI remain unverified.
  These checks do not claim the broader 1-second/10-second performance acceptance.

## Deployment and Safety

- Local pcrd **0.1.134** is active; capabilities and health were read back from it.
  Only its control daemon was normally replaced. All **5 worker processes and 24
  native processes** kept their PIDs/start times and original thread identities.
  Twenty native logs advanced in the observation interval; no health conclusion is
  drawn from the other four having no new output in that short interval.
- Stop states, pending commands, answers and follow-up queues matched across the
  switch. No production Agent retry, stop or finalize was issued. Relay reconnected.
  macOS **0.1.496** was not restarted or repackaged; its retry RPC uses the new daemon.
- Android **0.1.277 / code 278** was signed and published through the existing private
  Relay catalog. Read-only server verification confirmed availability and artifact
  digest/size. Relay **0.1.21** was not restarted. Temporary remote upload was removed.
- APK SHA-256: `e6669c97726175f67389462807265a06a04af3c54cb17283ffb03e1ea7749290`.
- Wheel SHA-256: `c1bff70dbee8bfab479acec49fc237974b62f5c0c3ee734354dcdfa713ba8167`.
- `release/manifest.json` contains the new wheel and APK with matching hashes.
  `apps/android/dist/Parallel Codex.apk` is updated. The owned read-only emulator
  was stopped; existing production runtimes are retained for their active workers.
- Detailed receipts: `~/Library/Caches/ParallelCodexMaintenance/Retry134-20261031/`.
