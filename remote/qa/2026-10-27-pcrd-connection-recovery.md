# pcrd Historical-Read Congestion Recovery, 2026-10-27

## Diagnosis and Repair

The live LaunchAgent still used pcrd 0.1.105. Capabilities and the existing
authenticated Relay connection worked, but workspace/run queries timed out.
Native samples showed long parsing work and shared-lock waiters. The affected
event log contained 587,971 records / 4,453,172,363 bytes. Its size was not a
reason to remove history.

The reproducible mechanism was a long event-lock critical section: history
filtering performed optional command classification while holding the shared
event lock; a manager-lock owner waiting for that event lock blocked unrelated
management requests. The old classifier also tokenized long shell scripts
before deciding not to classify them. A separate cold summary path could
replay the complete event log synchronously.

Repair in pcrd 0.1.108:

- Snapshot at most 1,000 raw records / approximately 512 KiB per event page,
  then release the append lock before JSON decoding and transcript filtering.
  A first oversized record can still advance, as before.
- Preserve the true cursor and `has_more`, including empty filtered pages.
  `get_events` classification also runs outside the manager lock.
- Skip optional shell classification before tokenization for dynamic/multiline
  commands or inputs over 16 KiB. Raw commands, events and operation IDs remain
  intact; these commands use the existing generic-command presentation.
- Bound summary/synthesis cold replay to one page per run per refresh. Retain
  its cursor and do not present partial historical activity as current activity.

Live Python frame attachment was denied by macOS. No elevation or security
bypass was used. The native samples support the incident diagnosis; the exact
live Python line/first triggering request was not obtained. The lock mechanism
was independently reproduced using the installed 0.1.105 code in isolation.

## Verification

- Complete source suite: 1,246 tests and 322 subtests passed.
- Installed 0.1.108 wheel: 163 focused tests plus the additional management-lock
  isolation regression passed. `pip check` passed; the four repaired installed
  modules exactly match source.
- The isolation test pauses real history classification and checks that real
  manager health still returns. It fails with installed 0.1.105 and passes with
  installed 0.1.108. No production message was used as a test execution.
- Additional coverage includes bounded nonmatching pages, cursor continuation,
  cold summary replay, and restart of a journal with a stop already applied:
  the original `started` stop remains in doubt and is not executed again.
- The existing native consumer regression
  `WorkspaceReadinessTests.interleavedConversationAndWorkPagesMatchTheCompleteSourceProjection`
  passed against current macOS source: an empty advancing conversation page is
  retained and subsequent output/work pages merge without loss or duplication.
  The first attempt used the default SDK 27 and failed on a missing SwiftUI
  macro plugin; the established SDK 26.5 / native build configuration passed.
  No Swift source or installed client bundle was changed for this check.
- Read-only retrieval at the actual long-script cursor 531,644 returned its
  original command/operation ID in 1.62 s. Output-only filtering returned an
  empty page in 1.08 s with cursor 531,646 and `has_more=true`, not false EOF.

## Activation and Live Acceptance

After checking all recorded runs were terminal, all seven original workers
were durably idle and in independent process groups, and no prompt/follow-up
delivery was pending, only the old manager was retired with `launchctl bootout`.
The old PID exited before the replacement was bootstrapped. The LaunchAgent's
only configuration change was its interpreter path to runtime 0.1.108.

Actual manager: PID 74324, started 2026-10-27 13:44:54 Asia/Shanghai, capabilities
report 0.1.108. The existing macOS 0.1.476 client, PID 39012, was not restarted;
it automatically recovered workspace statuses, conversations and history.
A later read-only UI check showed the memory-decoding workspace with loaded
history, no disconnected/unverified-workspace message, and both synthesis
Agents 2/3 explicitly displayed as stopped. No workspace click was simulated.

The final acceptance pass at 13:56 Asia/Shanghai measured workspace listing
0.229 s, run listing 0.577 s, the original large-run summary 0.057 s, health
0.573 s and an authenticated Relay update check 0.332 s. A prior independent
pass returned the same queries successfully. An earlier 8-second health timeout
during initial backfill is retained as an observation; this does not establish
that every future disk/network read is guaranteed to finish within one second.
The replay limit is per run/refresh, not a universal wall-clock guarantee for
an entire multi-run request. Run-list summary construction is paginated (at
most 100 runs); lifecycle maintenance still has its existing manager lock.

The seven worker PID/start-time identities, original run/thread identities,
original event file size/inode/tail digest and original pending stop receipt
were unchanged across the replacement. The two user-stopped synthesis Agents
remain `killed`. No task was retried, finalized or resubmitted. No research
directory, history, credentials or durable client message store was cleared or
rolled back. The old stop receipt remains `started`/in doubt; it was not forged
as completed and does not prevent read recovery.

## Delivery and Boundaries

Local immutable wheel and updated release manifest:
`release/pcrd/parallel_codex_runner-0.1.108-py3-none-any.whl`, 1,190,230 bytes,
SHA-256 `4928c7e3eb6c9b9ad735512490280644f5a1295b4f07b68118c3ee65c809c1f9`.
All manifest artifact hashes and sizes were checked. This manager repair did
not rebuild macOS/Android, replace the installed Codex plugin/shared Python
runtime, or redeploy Relay. Runtime 0.1.105 and original worker runtimes remain
available; only this incident's disposable build tree and duplicate wheel
staging are removed. No claim is made that all historical pages have finished
backfilling or that independent remote research jobs were audited in this fix.

Private samples, test XML, exact source patch, sanitized probes, before/after
safety snapshots and activation record are under:
`~/Library/Caches/ParallelCodexMaintenance/Connection-20261027-RsjMim/`.
