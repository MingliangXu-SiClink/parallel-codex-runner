# Queued Follow-Ups During Synthesis Refresh

## Incident

The reported run was `pcr-20260919T031718Z-baf8ea0a` in the paper-study
workspace. All times below are UTC on 2026-09-19, read from persisted events.

- 03:19:18: the initial candidates and synthesis completed.
- 03:20:56: another candidate was requested.
- 03:21:59: that candidate completed and triggered another synthesis attempt
  using the existing synthesis Agent ids.
- 03:23:48: synthesis actually finished.
- 03:23:51 to 03:23:58: the queued continuation finalized the selected result.
- 03:24:00: the queued message started as `pcr-20260919T032400Z-57c10d2a`.
- 03:27:42: that continuation completed.

The message had already advanced before this repair. It was not manually resent.
The screenshot's all-green state was incorrect: synthesis was still running.

## Root Cause And Repair

1. The daemon treated terminal status as irreversible for an Agent id. A synthesis
   refresh reused the id without clearing the preceding attempt's result, timing,
   or usage. `synthesis_started` now explicitly starts a new attempt, clears those
   projections, and recomputes the recommendation. Stale synthesis cannot be
   accepted or continued. Late progress within one attempt remains guarded.
2. macOS independently preserved cached terminal states over authoritative working
   snapshots. Current or newer snapshots can now transition the same Agent into a
   new attempt, including after relaunch.
3. macOS queue presentation now retains the daemon queue phase and distinguishes
   candidate execution, synthesis, result saving, and next-turn dispatch. Countdown
   branch text comes from the daemon selection rather than the Agent being viewed.

The queue still waits for the worker to release the current result files. It does
not force finalization while synthesis is writing them or bypass the user's delay.

## Automated Verification

- Reproduced the backend regression with three failing assertions before repair.
- Reproduced the client regression with both equal and newer event-count snapshots.
- Backend regression suite: 324 tests and 28 subtests passed.
- Built wheel installed into an isolated import location: 155 tests and 2 subtests
  passed against that wheel, not the source checkout.
- macOS client suite: 128 tests in 19 suites passed, plus the script's contract checks.
- Signed release build and archive validation passed.

## Real Client And Worker Verification

Only an isolated QA workspace was used for synthetic prompts; no user run was
cancelled, finalized, or resent for testing.

First pass:

- Source run: `pcr-20260919T042024Z-4acd9b8b`.
- After all results completed, added one candidate and observed synthesis reset
  through queued, copying, running, and success.
- Closed the QA app before completion. The daemon dispatched the queued message
  exactly once as `pcr-20260919T042602Z-b3e3cae1`, which completed successfully.
- This pass exposed the additional macOS cached-success bug. The first build was
  not published; it was corrected, retested, and rebuilt.

Final signed build:

- Source run: `pcr-20260919T043122Z-5d3b9b33`, one candidate plus one synthesis.
- Used the native app's Add Agent control after completion, selected Agent 1, and
  submitted a real queued prompt with the composer button.
- Native accessibility and screenshot inspection showed synthesis as working and
  the queue as waiting for synthesis. Selecting synthesis showed a new elapsed
  timer and no usage carried over from its preceding attempt.
- Closed the QA app during synthesis. Observed countdown, dispatching, then an
  empty queue. The next run `pcr-20260919T043523Z-81b819d9` completed exactly once.
- Relaunched the app: both questions and `QUEUE-UI-NEXT-OK` appeared, with no stuck
  queue. Both QA continuations were finalized and the QA app/daemon were stopped.

Service-restart acceptance:

- Started `pcr-20260919T045921Z-4e94aceb`, queued one continuation, and gracefully
  restarted the isolated daemon while its Agent was running.
- Daemon PID changed from 97647 to 97755; worker PID 97649 remained alive and was
  reattached with the queue intact.
- The background continuation `pcr-20260919T050019Z-40d4410a` completed exactly
  once, then was finalized. The isolated daemon was stopped afterward.

## Release

- macOS 0.1.372 (372), signed with Parallel Codex Local Signing.
- Relay release: `macos-release-5fdadf293e6941489d09079f0c41532e`.
- App archive SHA-256:
  `a591715efafe9913d4ea8701703c29afcf1d503b1806173e4b5bd80dcc1ef511`.
- Authenticated update check, ticket creation, download, size, and digest verified.
- Installed into `/Applications/Parallel Codex.app`, verified signature, and
  relaunched. The user's active conversation, queue, and running Agents restored.
- pcrd 0.1.49 installed and activated. The idle-only deployment attempt made no
  changes while user work remained active. After explicit user approval and the
  isolated live-restart test, gracefully restarted only the production daemon.
- Production daemon PID changed from 69326 to 98927. Active worker PID 87643 was
  unchanged; its event count advanced from 6313 to 6320 and then 6377. The queued
  item id was preserved and Relay reconnected without startup warnings.
- Native macOS inspection after reconnection showed two synthesis results complete,
  one still working, and the accurate waiting-for-synthesis queue text.
- Existing web servers kept PIDs 68987 and 78682 on ports 8920 and 8950; both returned
  HTTP 200 after deployment. No Agent or web server was stopped for deployment.
- No Android or Relay binary change was required. Android device UI was not tested
  in this round; the backend state repair is shared by both clients.

Logs are in
`~/Library/Caches/ParallelCodexMaintenance/QueueSynthesis-20260919-v6QD6b/`.
