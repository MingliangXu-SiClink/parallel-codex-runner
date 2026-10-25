# macOS Save Gate and Continuous History Repair

## Scope and Evidence

Client baseline: 0.1.471. Candidate: 0.1.472. Daemon/runtime remains 0.1.105.
No backend capacity-policy, task, account, answer-delivery, adoption protocol,
queue identity, updater, or signing-policy changes.

Private evidence is under
`~/Library/Caches/ParallelCodexMaintenance/ConversationGate472-20261023/`.
Earlier queue incident evidence remains under `QueueFinalization-20261023/`.
The user's original message was not resent or used as a test fixture.

The earlier movement-decoding continuation had a completed finalize/adopt/cleanup
receipt before the client screenshots still said saving. Its original message
subsequently received `confirmation_required`: approximately 6.56 GiB estimated,
5 GiB confirmation threshold, 37.35 GiB free, and `fits=true`. The original
confirmed command later started its child with native execution evidence.
The screenshot alone does not identify the precise delayed read or its duration.

For the newly reported fine-hand workspace, bounded observation of the reported
run and its continuations found a later complete cache containing 140 history
entries (38 user and 102 output) and 11 Agent transcripts. The production Swift
decoder accepted that record. A targeted history request timed out once. This
does not establish native-history loss or cache-decoder failure. The exact cache
contents and scheduling order at screenshot time were not captured.

## Reproduced Causes and Fixes

1. `acceptSelectedAgent` held `.useResult` across the finalize mutation and
   subsequent history, prompt-history, and health reads. The continuation planner
   therefore classified read-only waiting as a write operation and queued input.
   The mutation now releases its existing operation gate after processing its
   receipt. Follow-up reads run independently with the original machine,
   workspace, and run guards. Lost-reply verification and genuine unfinished
   finalization semantics remain unchanged.
2. A partial foreground page could be cached on the production workspace-switch
   path before its history reader completed. The existence of warm state then
   short-circuited restoration of the complete disk record, and the delayed
   persistence replaced that full record with the partial page. New regressions
   failed on the original code both with and without flushing before return.
   Incomplete warm records now hydrate and merge existing same-run coverage;
   disk persistence merges the current stored record rather than discarding its
   history. Current selections and newer transcript cursors retain priority.
3. A failed history read could not restart while an unrelated Agent reader was
   still active, because the Agent-task guard preceded history scheduling.
   A held-Agent regression reproduced the timeout. History scheduling now uses
   its existing independent task guard before that early return.
4. Capacity confirmation now shows the server-provided threshold and distinguishes
   threshold confirmation from genuinely insufficient space. The explanation
   wraps instead of being truncated. Older receipts without threshold fields
   keep a neutral explanation. The original confirmation action, message ID,
   space recheck, and 5 GiB server policy are unchanged.

Cache keys remain machine/workspace/run scoped. The merge does not transfer a
parent's complete-history flag to a different child key. The existing disk lock
covers read/merge/atomic-write together, and existing timestamp ordering rejects
a stale record arriving after a newer commit. No new cache schema or second
draft store was introduced.

## Verification

Targeted native/loopback regressions verify:

- A completed save returns while history and health reads are held; the saving
  gate is clear, the draft is preserved, and exactly one finalize request occurs.
- A newly accepted child remains visible when the parent's late reads complete;
  carried history and the child's unsent draft remain intact.
- Real workspace switch-away/back restores older questions and answers from
  disk, including when the partial page was flushed first. The native offscreen
  timeline contains the earlier answer without any production command.
- Delayed disk hydration restores history while retaining a newer reply,
  explicit Agent selection, and finalized state. Both single-run and workspace
  hydration paths pass, including cold recovery after an out-of-order old write.
- History retry succeeds while another Agent's reader remains held.
- Capacity reason rendering covers sufficient space above the threshold,
  insufficient space, and legacy receipts, without automatic confirmation.

The first broad strict-executor run also exposed two unchanged legacy socket
fixtures that block the cooperative executor, plus the independent-history retry
race subsequently fixed above. Its failure log is retained. Canonical and final
focused gates must be checked separately; an isolated passing run is not used
to erase that evidence.

- Frozen final canonical native gate: 692 tests in 63 suites passed. The two
  unchanged legacy socket fixtures also passed under the canonical executor.
- Frozen strict cooperative-executor focused gate: 111 tests in 6 suites passed,
  including workspace readiness/navigation, queue behavior, capacity rendering,
  client lifecycle, and signed updater checks. No timing assertion was relaxed.
- Signing-store, no-signing, contracts, and app-icon gates passed. The first-paint
  PNG was rendered from the native offscreen conversation surface with health
  held, showing both cached history and the current answer. The window was never
  made visible or focused.

## Publication and Installation

- 0.1.472 / build 472 was signed with the existing `Parallel Codex Release
  Signing` identity and strict-deep verified. Formal Relay catalog readback
  matched its version, build, hash, size, and signer; no revocation was present.
- Archive SHA-256:
  `baff92c0ce6b7324b869e0adbab89e7088c7404973a49cf691bd8b2693daf940`.
  Size: 14,972,171 bytes.
- The existing authenticated controller's update check returned `IpcError`.
  No token refresh, new bridge consumer, authentication change, or validation
  exception was used. The local copy of the exact formally published archive
  passed the unchanged installed native updater's signature and bundle checks.
  Public/authenticated download-path success is not claimed.
- A separate bounded runtime check returned daemon 0.1.105, paired controller,
  and `system.health=ok` before installation. That local health response does
  not establish Relay business-path or every remote compute job's health.
- Fresh CUA and durable-state checks found one real main window, an empty
  composer, no active question editor, no PDF preview or modal, and no pending
  local inputs/queues requiring intervention. Outboxes, accepted handoffs,
  drafts, answer bodies, navigation, and service queues were checked again
  immediately before normal client termination.
- The existing native installer installed 472 and relaunched in the background
  at 2026-10-23 03:57 UTC. PID changed from 34139 to 96630. Full installed bundle
  hashes matched the signed publication, and the loaded executable inode matched
  the installed file. Loaded executable SHA-256:
  `7e8c459b06e9e5356c6d3496011f773bb5664ed4f55bfc5b483bd90fad2fff0e`.
- All 11 original worker processes and 5 native Codex processes retained their
  PID/start identities. Daemon PID/runtime, LaunchAgent, receipt store, original
  drafts/answers, and queues were unchanged. No task command, answer, forced
  termination, worker restart, or user-data rollback occurred. The installer
  does not infer task or remote-compute health from those process checks.

## Installed Workflow Check

The restored fine-hand workspace showed the same selected synthesis Agent 3,
an empty continuation composer, work disclosure, and 44 question navigation
markers. Earlier questions/answers and the latest question/answer were both
present. A real read-only UI switch to the movement-decoding workspace and back
again showed both in the first observed returned view; a subsequent read after
background refresh remained consistent. No false earlier-turn/workspace-current
banner or stuck-saving status appeared. No message or answer was sent to test it.

The latest fine-hand run's private cache then contained 151 history entries
(43 user and 108 output), 11 transcripts, and `historyLoaded=true`. This is a
scoped installed workflow check, not reconstruction of the old screenshot's
precise timing or proof that every historical navigation sequence is covered.
Subframe first-paint latency was tested in native fixtures, not inferred from
CUA observation latency. Evidence includes `client-install-result.json`,
`runtime-install-before.json`, `runtime-install-after.json`,
`post-install-window-check.json`, and `observation-20261023T035843.json`.
