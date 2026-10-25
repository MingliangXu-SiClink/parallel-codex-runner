# Parallel Startup and Paginated Resume Repair

## Scope

- pcrd / PCR package 0.1.65; Codex CLI 0.159.2, matching the installed
  desktop client's history format. This is a validated compatibility version,
  not a claim that it is the newest npm release.
- Installed macOS client remains 0.1.393 / build 393. Its executable matches
  the existing release archive and passes strict signature verification.
  This repair does not change macOS, Android, or Relay client code.
- Unrelated, pre-existing source-layout migration and working-tree edits were
  preserved. No running research Agent or existing web service was stopped.

## Root Causes and Repairs

- The original five-candidate run spent 1,411.584 seconds before launching all
  candidates. Sampling located CPU time in path-discovery regexes over large,
  slash-rich history strings, including base64 tool output. Literal marker
  checks and a PCR-specific bounded path pattern replace the quadratic scan.
  Copy, workspace rebinding, and orphan-output cleanup now share one atomic
  streaming pass instead of repeatedly parsing the same history.
- The selected history is a paginated fork with two inherited source segments.
  Copying only the current rollout and one thread row leaves its source lineage
  absent in an isolated Codex home. Resume now resolves exact inherited byte
  and ordinal cutoffs, including on-disk source files whose database row is
  missing, and materializes a portable legacy copy in the private Agent home.
  Ambiguous or invalid ancestry fails explicitly without modifying originals.
- The full inherited history is 1,800,280,506 bytes. Real Codex restoration
  peaks at approximately 2.6-3.2 GiB per process. Restoring five such histories
  simultaneously caused excessive temporary memory pressure and request
  timeouts in the first repair attempt. A cancellable per-run file lock now
  serializes only restoration of histories at least 128 MiB. Once resumed,
  Agent inference and tools remain concurrent. Timeouts name the failed RPC
  phase instead of yielding an empty error message.
- Model catalog identity now uses the resolved CLI executable/version and a
  stable ChatGPT account/user partition, not rotating OAuth tokens. Failed
  refresh retains an existing same-version snapshot with its original age;
  it cannot turn another CLI version's cache into current metadata. Live pcrd
  verification returned GPT-6.1 Sol and its supported effort choices.

## Verification

- Evidence root:
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ParallelStartup-20261002`.
- Targeted source tests: 83 passed plus 18 subtests. Installed final wheel:
  72 unit tests passed. An additional 51 tests plus two subtests passed for
  workspace snapshots, synchronization, and timing.
- The broad bare-Python run executed 461 tests successfully but had two test
  module import errors because that interpreter lacked pytest. Those modules
  passed in the project's test environment. The original broad invocation
  is not reported as a fully passing 463-test run.
- Five official Codex app-server restores passed in 62.304 seconds. This QA
  sent no `turn/start`, performed no inference, and did not modify the user's
  workspaces. It verifies restoration, not manuscript-task completion.
- The 1.8-GB history prepared in 5.925 seconds. All three original history
  source hashes remained unchanged. Tests cover ancestry boundaries, source
  isolation, atomic failure, slash-rich strings, cancelled waiters, and
  execution overlapping after each restore releases the lock.
- The first installed repair, 0.1.64, failed the real five-Agent continuation
  despite single-restore QA. That failure prompted the memory-pressure fix;
  it is not treated as successful acceptance.

## Deployment and Authorized Rerun

- Updates were activated after the original task and the failed first retry
  became inactive. The LaunchAgent now selects the immutable 0.1.65 runtime.
  The original worker's pinned runtime was not overwritten.
- Final wheel SHA-256:
  `ad02e5aaede1c7ec095a306b0b3fa9cdbe66a0a53f2dc8c63976932b104e263b`.
- An activation helper initially stopped at model-catalog validation after
  the service had already switched successfully. Read-only final verification
  subsequently passed service health, pairing, CLI version, model catalog,
  installed app/archive equality, and signature checks. It did not restart
  pcrd a second time while the new task was running.
- The exact authorized original question was resubmitted once on 0.1.65;
  its prompt, conversation, workspace, model, effort, and Agent counts were
  preserved. The idempotent command and new run are in `rerun-0.1.65.json`.
- New run: `pcr-20261002T035051Z-3a166978`. All five candidates launched after
  80.165 seconds. At 12:00:57 China time, all five were running and each had
  completed real command executions and web searches. The native formal
  client showed the current five candidates, live usage, and work items;
  `formal-client-live-rerun.png` records the display.
- `deployment-0.1.65.json` records these live checks without exposing prompt
  contents, tokens, credentials, or command output. The research question
  itself was still in progress, not completed, at this acceptance point.

## Limits and Follow-Up

- All five candidates initially showed Codex connection retries before real
  work began. They recovered, but the cause of that network delay has not been
  established. The 80.165-second comparison measures PCR preparation and
  process launch, not first model response latency or total task duration.
- Fresh model catalog requests can still time out; existing snapshots are
  retained. A further cache-key audit of unrelated configuration changes is
  separate from this startup repair.
- No real Android device or synthesis-result completion was accepted in this
  round. The three configured synthesis Agents start after candidate results.
- Independent ChatGPT Web consultation was unavailable because the existing
  account session reported a temporary usage restriction. No private research
  prompt or credentials were submitted to that webpage.
