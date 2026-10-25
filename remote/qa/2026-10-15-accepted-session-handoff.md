# Accepted saved-session handoff

## Scope and observed production state

Client candidate: macOS 0.1.432/build 432, continuing the unreleased 431
background-reading work. No pcrd source or protocol change is required.

Read-only inspection found the official loaded executable is macOS 0.1.430,
PID 67550, inode 184059749. The authenticated daemon reports 0.1.86, not
0.1.88; the latter remains a published/staged backend, not an activated one.
The user-paused `pcr-0-1-88-430` automation remains PAUSED.

The affected message already has a completed command receipt and its run has
finished. Its original text, command/client identity, workspace ID/path,
requested resume session and returned run configuration agree. No message is
missing and the task does not need to run again.

## Root cause

There are three distinct submission contracts:

- An explicit continuation names a source run and Agent; both must match.
- A saved-session start names a resume session, without explicit source run or
  Agent. pcrd may correctly resolve that session to an adopted parent run/Agent.
- A fresh start names neither; it must not unexpectedly inherit a parent/session.

Client 430 incorrectly applied explicit-parent equality to all three. In the
observed saved-session case, absent request parent IDs were compared with the
daemon's correctly populated canonical parent and treated as a workspace conflict.
The durable accepted marker was consequently retained and blocked the composer.
The error message obscured which predicate actually failed.

## Repair

One common destination validator now checks the original entry contract rather
than treating absent explicit parent IDs as a prohibition on resolved ancestry.
Saved-session starts must match the exact original request session and persisted
session identity; fresh starts still reject unexpected sessions/parents. Explicit
continuations retain exact source-run and source-Agent checks. Existing run,
message, workspace and command/client checks remain, including workspace identity
on both embedded and explicit conversation snapshots.

The original conversation selection can be a native-session or historical run
navigation alias. It is not an additional execution source supplied to pcrd.
Canonicalizing that display key must not manufacture another conflict when the
authenticated command, workspace and requested session match.

Mismatch diagnostics now identify the failed field instead of mislabeling every
case as a workspace mismatch. Genuine conflicts still retain the receipt; there
is no warning suppression, manual state editing, identity change or automatic
resubmission. An old `opening_failed` marker uses the existing read-only handoff
reconciliation path on the corrected client, including after restart/offscreen.

## Evidence and acceptance

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AcceptedSessionHandoff-20261015`

- `red-tests.log` reproduced both immediate and restart/offscreen failures on the
  prior guard, before the repair. The original marker remained, with no durable
  destination. `green-first-tests.log` verifies those same cases after repair.
- `capture-readonly.py` fetched only capabilities, the affected run and the exact
  original `commands.get` receipt. The original desktop-state file was unchanged
  across capture. The private fixture remains outside the repository, mode 0600;
  its prompt and full run data are not copied into this QA report.
- The actual captured run/receipt pass the production Swift validator offline,
  not just a fixture that mirrors client assumptions. Changing its resume session
  is still rejected. The old parent-equality check demonstrably rejects it.
- Isolated socket cases cover saved-session continuation after stopped/discarded
  turns, canonical and absent parents, complete answer/timeline appearance,
  persistence and cold-cache readback, restart, later drafts, offscreen settlement,
  fragmented/late replies and rapid navigation. Assertions include exactly one
  original command and no resend, not only disappearance of a warning.
- Negative cases retain and diagnose wrong run/text/workspace/command/client,
  missing/wrong session, partial explicit source, partial resolved ancestry and
  unexpected fresh-start ancestry. Corrected reads can settle the same receipt
  without another submit.
- The existing isolated backend test for promoted-session lineage passes using
  the source checkout and its configured virtual environment. No real user turn
  or remote connection is submitted by that test.

The first full-suite run exposed a readiness-test race: terminal *page status*
was incorrectly used as a complete-answer wait barrier. The assertion now waits
for both terminal status and pagination EOF before comparing the entire answer.
This strengthens the wait condition; it does not change production paging or
shorten/omit any answer. The 100 ms native warm-reading timing assertion is not
relaxed; final results are recorded below after verification.

An anonymized independent technical consultation was submitted in the existing
embedded-browser discussion. On reopening the same conversation its answer again
displayed "unable to load this response"; no readable partner analysis was
available, and no agreement or external verification is claimed.

## Deployment boundary

No official-client install/restart, scheduler activation, update-feed publication,
real task mutation or live desktop-state repair is performed during these checks.
The paused deployment is not resumed. Building/signing a candidate is not evidence
that the running 430 client has repaired its retained handoff.

## Final verification and candidate

- `client-final-tests.log`: **478 tests in 53 suites passed**, including actual
  captured-receipt verification, full prompt delivery faults and the previous
  background-reading work. Standalone protocol/desktop-state contracts, generated
  protocol check and all icon checks passed. Foreground interactive QA was not run.
- `strict-tests.log`: **35 tests in five suites passed** with
  `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1`, including source identity validation and
  background cache/navigation faults. Legacy blocking fake-socket suites are not
  placed on a single cooperative worker.
- The initial full run's native timing observations (110.8/302.0 ms) are retained
  in `client-full-tests.log`, not erased or turned into passing assertions. The
  unchanged timing test subsequently passed in both the strict run (21.761 ms
  slowest native appearance) and final full run (20.485 ms); all forty answers
  were actually present. These are fixture measurements, not a guarantee against
  arbitrary system-load/rendering delays. Warm data switches in the final run
  had p95 0.907 ms and maximum 1.345 ms over fifty workspace/400 Agent switches.
- The final native PNG was visually inspected: workspace 4/Agent 8's complete
  answer and all three history pages are readable, not a loading placeholder.
- Source/test/script/resource hashes match before the final tests, after tests
  and after release construction. Hash-manifest SHA-256:
  `04c647e7543c0301a2ec757d6f95043287234e9f246c1eb184766ec3bf0a9a4e`.
  Info.plist hash is
  `bb92b6541fa884d93e7cb00b811b9c21bce1bf5f5bc26afc903c2e3edf432573`.
- Release construction succeeded for **0.1.432/build 432**. The built application
  and extracted archive passed strict/deep signature verification and packaged
  math/syntax rendering. Signing authority is `Parallel Codex Local Signing` with
  the existing published certificate root
  `7cf98a08b97b137a2952b8f39c71531b62f6403f`.
- Candidate ZIP:
  `AcceptedSessionHandoff-20261015/Parallel-Codex-macos-0.1.432.zip` under the evidence
  directory. SHA-256:
  `aa9078102f927c3bd8200662fcbbba4d1cbf6dd5afebc3d4d424e27527984f3e`.
  Executable SHA-256:
  `380d6071434c103c221b679388ada95f467efaa10e4fd967eee9b3d1054c5861`.

Final read-only verification still finds official PID 67550 and the original
executable inode, client 430 and daemon 0.1.86. The exact accepted message marker
is retained in the running old client. The local release manifest is unchanged
(macOS 430/pcrd 0.1.88); SHA-256:
`d5c80abf868d627359caf43357cf68330a458d961b36f0b02ab6b3132e25a490`.
The 0.1.88 wheel is unchanged:
`3b4233e08fd4facba85d7a65829379638f7f311445b71a229b602f287174f55e`.
No installation, restart, publication, real-message replay or live-state edit is
claimed. The new client's automatic reconciliation is verified in isolation; it
cannot run inside an unchanged 430 executable.
