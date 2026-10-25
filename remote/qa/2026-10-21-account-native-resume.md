# Account Changes And Native Recovery

## Scope

macOS 0.1.468 distinguishes signed-in-account limits from active execution
bindings. pcrd 0.1.105 follows an unambiguous ChatGPT file-backed account for
new execution owners and preserves the existing native recovery contract.
The locally built 0.1.103 and 0.1.104 candidates were not activated: the final
105 includes authoritative latest-native-turn quiescence and isolates the
background quota reader from the primary login.
Neither publication nor a management/client restart migrates pinned legacy
execution owners. Their original sessions and running computations are not
interrupted by installation.

## Root Cause

`copy_codex_support_entries` originally copied `auth.json` once and skipped an
existing private copy. Long-lived native processes also retained authentication
in memory. Changing the main Codex login therefore did not change those native
owners. The usage reader independently queried the main login, which could
describe a different account than the one attached to a running Agent.

## Implementation

- Preserve the logical selected-home path, including account-switcher symlinks.
  Bind identity to both workspace/account and user, not token renewal or plan.
  Usage-cache identity now distinguishes different users in one workspace.
- Copy only private authentication before launching a native writer, under the
  original execution lease. Never copy sessions, reset a workspace, or overwrite
  the primary login. Do not copy credentials over an existing native writer.
- When the selected account changes, wait for tools, answer delivery and open
  questions. End only the native reasoning turn. Retain its original receipt,
  thread, workspace, input identity, logs, files and results.
- Require authoritative native terminal/loaded-thread inventory and a terminal
  latest native turn before replacing the process. Unknown state, extra native
  child sessions and live terminals defer the switch. A lost input receipt is
  not permission to replay the task or close an unverified active turn.
- Reap the old writer before copying the selected authentication, then use
  native `thread/resume` and the existing recovery reconciliation. Only the
  unfinished continuation receives recovery guidance, never the initial task
  text again. Validate the original thread and workspace on resume.
- Same-account token renewal stays native and private to its execution home.
  Do not use a host OAuth refresh broker that can write a stale refresh response
  into the primary login. User stop intent always suppresses automatic recovery.
- File-backed quota reads use a disposable native server with access-token-only
  ephemeral authentication. They cannot refresh or overwrite the primary login;
  expiry is unavailable rather than a fallback to another account. Source changes
  during a read cannot publish old limits under the newly selected account.
- A retry of a receipt-bearing failed candidate prefers retained native recovery.
  Live owners, malformed/uncertain receipts and unsafe bindings cannot silently
  fall back to a fresh workspace/full-task replay. Unsubmitted retries retain
  their existing behavior.
- Usage details identify current, waiting, legacy and unverified bindings.
  These are execution-binding observations, not per-request billing evidence.
  Stale/error responses do not keep an old execution summary labelled current.
  API-key, custom-provider and keyring/auto sources are not silently treated as
  a file-backed ChatGPT identity or claimed as automatically migrated.

The backend wheel also includes the previously audited four-line native-image
projection change in `remote/transcript.py`; no research directories are moved.

## Verification

All model traffic in native acceptance is synthetic and loopback-only, with an
isolated Codex home, external proxies blocked, and no production input or token.
Actual Codex CLI 0.159.2 performed native account/routing reads, original-thread
resume, real local commands and native managed token refresh.

| Case | Native Evidence |
| --- | --- |
| A to B | Original thread retained; one original job; existing result retained; subsequent inference uses B |
| B token expiry | Native 401/reload/refresh yields B2 in the private home; primary login remains unchanged |
| Late B refresh while C selected | Old writer cannot overwrite C; original thread continues under C; original job is not repeated |
| Rapid changes / stop / rejected authentication | Late acknowledgements cannot claim the next account; stop wins; no original-task replay |
| Unknown active native turn | No new native owner or credential replacement before authoritative quiescence |
| Native quota reads / expiry / source change | No model turn, OAuth refresh or primary-login write; old results cannot be committed for the new source |
| Legacy recovery / questions | Existing recovery, lost receipts, blocking questions and async answers pass their native acceptance suites |

- Source Python gate: 1,238 tests and 318 subtests passed.
- Independently installed 105 wheel: 1,237 tests and 318 subtests passed;
  only the repository-vendored-path assertion was excluded. Every installed
  package file was compared with the immutable wheel.
- Installed-wheel native acceptance additionally checked that every previous
  native writer was reaped and the original background job had finished before
  the next native process started. One original native history file contained
  the initial task exactly once and one/two recovery inputs for two/three turns.
  The first extra history assertion expected an `event_msg` user projection;
  this CLI persisted `response_item` user messages instead. The corrected reader
  checked those actual records; no production record or package was changed.
- macOS canonical gate: 672 tests in 63 suites passed; interactive foreground
  suites stayed disabled. Existing signed-update, lifecycle and question gates
  also passed.
- Signing uses the existing offline `Parallel Codex Release Signing` identity;
  no new identity, login-Keychain change or weakened verification.
- The default newer SDK failed to supply SwiftUI macros; the established 26.5
  SDK passed. An initial private-gate umask produced 0600 fixture files where an
  existing diff test expects 0644; the canonical 0022 test umask passed.

Timestamped gate hashes, negative probes and native evidence are private under
`~/Library/Caches/ParallelCodexMaintenance/AccountResume-20261021/`.

## Delivery

macOS 0.1.468 is formally published to the existing authenticated update channel,
downloaded through the existing pairing and accepted by the native verifier.
Publication, staging, installation, management activation and real legacy-Agent
migration are separate outcomes; the final local delivery evidence records
which were actually completed. No legacy task migration is inferred from a new
application version, a live PID or a `running` label.

pcrd 0.1.105 is independently installed and enabled. The original management
PID exited before the one new daemon started. Initial socket/30-second health
reads and a later bounded read timed out; the management process was not
restarted again. Subsequent read-only reconciliation verified version, loaded
runtime bytes, pairing, connected Relay, idle delivery/queues, the original
receipt store and all ten original execution owners (PID, start time, group and
executable). Stack sampling showed lock waits/file IO; obtaining Python frames
was denied by macOS and was not escalated. The precise cause of the initial
read latency is not claimed to be fixed by the account change.

During reconciliation the actual main window became visible and the user added
one answer. This was not mistaken for data loss: the exact immutable hash of all
19 original answers was retained, and the additional answer matched its original
question/connection/answer ID and native delivery receipt. No answer was replayed.
The current-window observer separately identifies only the verified transparent
full-screen menu bar (layer 26, alpha zero, no children) and the tiny native
`WindowSharingSessionButton` surface. It still rejects other unknown windows and
real dialogs rather than mapping OS auxiliary surfaces to document UUIDs.

At 2026-10-21T13:51Z the native installer had replaced 467 with signed 468 and
normally relaunched it in the background (PID 18573 to 10007). The initial
post-install AX read preceded composer accessibility readiness; no second
installation or restart was attempted. A subsequent bounded observation found
the same empty composer, identical persisted draft/navigation/outbox/queue
hashes and identical answer identities. Strict signature, every published
bundle file, the loaded executable inode and SHA-256 were verified. All ten
owners and all nine previously live native execution processes were retained;
the daemon PID/runtime/receipts did not change during the client restart.

The actual 105 `codex.account_usage` read returned available limits and binding
counts `current=0, waiting=0, legacy=9, unverified=0`. This is deliberately not a
claim that those nine legacy executors have switched accounts. They were not
signalled or migrated, no real model turn/task command was submitted, and their
original work was preserved. Native account-follow acceptance applies to new
verified 105 execution owners, not retroactively to already pinned old code.

Final source hashes match both the signed client publication and the tested
immutable 105 wheel; manifest artifact hashes also match. The client executable
SHA-256 is `40693c57bed8b3ce7fb4ea25c93fdf01a72e620f9db00729e52bde2c9720d061`.

## Explicit Legacy Migration, 2026-10-21

The user subsequently authorized the actual migration. This is a separate
operational result from the installation above. Of the nine initially live
legacy executions, compression candidate 4 completed naturally at 14:37Z and
was not restarted. The eight still-running original candidates were handed
off in their existing directories:

| Original Run | Physical Candidates | Old Owner | New 105 Owner | Handoff UTC |
| --- | --- | --- | --- | --- |
| `pcr-20261019T142145Z-bd54a7be` | 2, 4, 5, 6 | 32194 | 24260 | 14:50Z |
| `pcr-20261019T143205Z-ba51dc4e` | 1, 2, 5, 7 | 34252 | 29224 | 15:10Z |

The existing maintenance quiescence and `resume_interrupted_batch` paths were
used, not a workspace-rebuilding retry. Original run/conversation, physical
candidate, command, private home, writable workspace, input/configuration hashes
and successful results were checked. The old in-flight operation request was
retained, old writers exited before target authentication was installed, and
retained execution fingerprints gated the new owner. Each original thread was
restored by native `thread/resume`; its unfinished work received the existing
recovery guidance rather than the original task again. A new recovery turn has
its own native input ID. The previous native receipt was retained as evidence;
this does not claim that every field of the current receipt is unchanged.

An important process detail was verified: these receipts named the Node CLI
launcher, not its Rust core. Capture, ownership checks and thaw/retirement used
the actual core PID, start time, executable and parent chain. Preflight and
frozen audits reconciled original stdout cursors, pending-question stores,
durable stop/control files and actual process descendants. Legacy stdio remained
owned exclusively by the old adapter; no second client claimed to query that
native server. Calls begun since the known zero-operation baseline were read
incrementally, not inferred idle from an arbitrary log tail or stale API label.

The compression foreground SSH calculation and native waits were allowed to
finish before its handoff. No remote process, job, WSL service or host was
signalled. Targeted read-only job evidence includes original process start ticks,
CPU progression, project ownership and naturally completed progress records.
The two owner groups were handled separately; no completed/stopped Agent was
added to make a recovery set fit. No concurrency or launch policy was changed.

Additional isolated verification used the installed 102 manager/supervisor and
fixed adapter, the real 0.159.2 Node/Rust processes and installed 105 recovery:

- Successful handoff: loopback inference moved from synthetic A to B; one
  original side-effecting job, original thread and saved result were retained;
  the independent background process kept its original PID/start time.
- Actual-core rejection/thaw: a safety veto after freezing the Rust core
  restored that same core, adapter and supervisor. Releasing the already
  accepted loopback response completed the original turn, with no replacement
  native process, recovery turn or additional original task.
- Focused recovery regressions: 18 tests and 37 subtests passed. Both existing
  maintenance-pause tests passed. Synthetic fixture setup/assertion corrections
  did not change any installed package or production record.

At 15:12Z all eight actual native bindings matched the selected account and
their exact running adapter source matched the installed 105 source. Every
resumed original thread had post-handoff assistant output or completed tool
events. Several initial WebSocket retries fell back to HTTP and then progressed;
acceptance was not declared from those retries or `turn.started` alone.
The actual account-usage projection reported `current=8, waiting=0, legacy=0,
unverified=0`. This remains execution-binding evidence, not a retroactive or
per-request provider billing audit.

The daemon stayed PID 4374/version 105 and the signed client stayed PID
10007/version 468 with the same executable hash. All eight other original
supervisors retained their PID/start time/group/executable. Pairing, receipt-store
identity, connected Relay, empty delivery/queues and local health were checked
separately. No application/daemon restart, adoption, new research task or
initial-task resubmission occurred. No further software release was needed for
this already-installed recovery path.

Timestamped intent, frozen audits, retired owner identities, native acceptance,
per-Agent post-handoff progress, remote job observations and final reconciliation
are private under
`~/Library/Caches/ParallelCodexMaintenance/AccountMigration-20261021T140321Z/`.
