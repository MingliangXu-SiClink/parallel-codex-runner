# Completed Results After Worker Loss

## Observed Failure

- The Mac booted at 2026-09-21 07:34:00 Asia/Shanghai. Workers belonging to
  already-completed runs from September 19 and 20 were no longer present.
- Their durable worker checkpoints recorded inactive, successful operations,
  but the daemon marked their workers as crashed and disabled adoption and
  continuation. The formal macOS client required discarding the current turn.
- This was a result-lifecycle defect, not missing conversation data: a completed
  result incorrectly depended on its original execution process staying alive.

## Change

- Restore completed-result adoption only when the original worker is dead,
  every recorded Agent is terminal, at least one result succeeded, the durable
  operation checkpoint confirms completion, and the run marker is valid.
- Reject recovery with pending requests, pending candidate operations, incomplete
  checkpoints, or an unresolved finalization journal. Existing workspace checks
  still apply to each result.
- Adopt through the existing locked and journaled finalization path; a follow-up
  starts a new turn and worker. Never restart or replay the lost worker's execution.
- Normalize previously misclassified completed and finalized runs, persist the
  change, and clear only the obsolete worker error. Preserve unrelated errors.
- Keep the existing prohibition on replacing a lost worker for unfinished work
  or adding/retrying candidates under another code version.

## Verification

- New recovery regressions failed against the old implementation.
- Source regression suite: 210 passed, 25 subtests passed.
- Built wheel installed into an isolated target: 224 passed, 25 subtests passed.
- Real detached-worker integration tests, in source and isolated workspace modes:
  finish a turn, kill only the idle QA worker, reopen the manager, adopt the saved
  result, promote its session, and finish a new turn in the same conversation.
  These tests use a deterministic fake Codex executable, not a paid model request.
- Recovery tests also confirm that source-mode adoption does not overwrite edits
  made by the user after completion, and ambiguous sync operations remain blocked.

## Deployment And Client Acceptance

- Installed pcrd 0.1.56; graceful scheduler exit code 0, new scheduler PID 45246.
  There were no active Agents or operations at deployment. No other service or
  the formal macOS client was restarted.
- Health: OK, Relay connected, no startup warnings.
- Restored `can_accept` and `can_continue`, with no worker error, for:
  - `pcr-20260920T135721Z-f2b90559` (siclab-next, one successful result)
  - `pcr-20260920T123348Z-689210de` (mouse experiment workspace, one result)
  - `pcr-20260919T032400Z-57c10d2a` (paper study workspace, eight results)
- The formal SiClab conversation refreshed from failure to awaiting selection.
  The discard requirement disappeared, the composer showed continue-question
  text, and its continue-from-Agent-1 button enabled when a temporary draft was
  entered. The draft was then removed without sending anything.
- No real conversation was discarded, automatically adopted, or replayed. The
  user remains in control of their next prompt and candidate selection.
- macOS 0.1.379 and Android 0.1.266 artifacts were reused; no client schema change.

Wheel SHA-256:
`f72b467b5ddce5f4ef3f85ecc2be02aa0cf4e318eae2fd5d6965aaf0f246b12c`

Rollback package and pre-deployment run state:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/worker-recovery-20260921-v4ftuh/`
