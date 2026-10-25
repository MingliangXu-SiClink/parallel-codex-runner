# Optional Worker Runtime Limit

## Confirmed Incident

- `pcr-20260921T163238Z-146ec676` started at 2026-09-22 00:32:38
  Asia/Shanghai and expired at 06:32:44. Agent 1 was cancelled with return code
  -15. The worker checkpoint confirms `expired=true`, exit code 130.
- The installed daemon reported `run_ttl_seconds=21600`. This was PCR's hidden
  six-hour default, not a Codex provider or account limit.
- The expiry path also removed the review baseline and private Codex home,
  despite pcrd retaining conversation history. This source-mode run's edits in
  `siclab-next` remain in the original folder. Its event/stdout logs survive,
  but the shared resumable Codex session only reaches 00:25:42, before this turn.
  Do not claim that the six-hour private session was restored or silently replay
  the original task.

## Changes

- Default execution timeout is disabled. Unset or zero
  `PCR_PLUGIN_RUN_TTL_SECONDS` produces no worker deadline; a positive value
  remains an explicit operator opt-in.
- Apply the same rule to initial turns, additional/retried candidates, and
  persistent-worker operations. New follow-up turns use the initial-turn path.
- Timeout no longer deletes partial work or private Codex sessions. Deletion
  belongs to explicit adoption/discard and the independent retention policy.
- Manual stop, artifact ownership checks, worker version pinning, and retention
  safeguards for active runs remain intact.

## Verification

- Regression tests failed against the old six-hour default and destructive
  timeout cleanup, then passed after the fix.
- Source suites: 299 passed, 38 subtests passed.
- Built wheel installed in an isolated target: 310 passed, 38 subtests passed.
- Tests cover default/unset/zero/invalid configuration, explicit positive limits,
  manual stop, partial-session retention, and a simulated 30-day active run
  surviving retention maintenance without a stop signal or cleanup.
- Real detached-worker tests cover manager shutdown/reopen, adding an Agent,
  and finalization in the same pinned worker, with no default deadlines. These
  tests use a deterministic fake Codex executable, not paid model requests and
  not a six-hour wall-clock soak.
- Formal macOS client inspection confirmed the stopped task and disabled
  continuation. No real task was discarded, relabelled successful, or replayed.

## Release

- Generated pcrd 0.1.57 and updated the local release manifest. Other client and
  Relay versions/artifact hashes are unchanged.
- Wheel SHA-256:
  `30bb5bcc74bcc49327019fbb66df6af5f4eed550ac4ad79e11fec3e846484476`
- After explicit user approval, installed this wheel into the existing pcrd
  Python environment and gracefully restarted only the scheduler on September
  22 at 08:14 Asia/Shanghai. The old scheduler (PID 834) exited with code 0;
  the new scheduler is PID 45254. Installed metadata reports 0.1.57 and live
  health reports `run_ttl_seconds=0`, no startup warnings, and Relay connected.
  The formal client and web services have not been restarted.
- Rollback package, original state, and validation artifacts:
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/RuntimeLimit-20260922-Ugmdz5/`

## Authorized SiClab Continuation

- Started `pcr-20260922T001727Z-efd8be30` at 08:17:27 Asia/Shanghai through
  pcrd's public `runs.start` API. It belongs to the existing conversation
  `pcr-20260918T105820Z-9143d234` and resumes surviving Codex session
  `01a0b42d-312d-74a3-b791-ba0adc2de78f`. The parent is the last successfully
  finalized turn; the expired turn was not discarded or relabelled successful.
- Preserved the previous configuration: one Agent, gpt-5.6-sol/max, no synthesis,
  source-workspace mode, sync-back enabled, and no retained candidate workspace.
  The new live run has `worker_deadline=""`, worker PID 45755, and Agent 1
  `running`. Codex emitted both `thread.started` and `turn.started`.
- Supplied a recovery handoff with the original task, interrupted iteration 26,
  surviving evidence, and explicit instructions not to overwrite current work
  or real business data. HEAD was `0f9d8602245799c39857c268b798e1a4509c4287`.
  The three existing dirty files matched pre-resume safety copies by SHA-256
  after scheduler activation and run preparation.
- The resumed Agent explicitly acknowledged continuing iteration 26, preserving
  prior work, inspecting the three uncommitted files and existing issue report,
  limiting verification to the repository's authorized compilation checks, and
  keeping port 8811 available. Its first completed command read current HEAD,
  git status, AGENTS.md, bugs_report.txt, and the existing service status.
- The formal macOS client shows siclab-next as `running 0/1`, the new recovery
  turn, and an enabled Stop Agent 1 action without restarting the client.
- The existing Node watcher (PID 21279) and web server child (PID 38073) both
  survived the scheduler restart and initial continuation unchanged. Port 8811
  returned HTTP 200. No business data reset or web-service stop was performed.
- This verifies successful task resumption, not completion of the remaining
  product improvements or restoration of the deleted six-hour private session.
