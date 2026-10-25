# Routing Timeout Recovery: pcrd 0.1.90

## Observed Incident

The requested workspace is the memory-state cross-subject/cross-day decoding
workspace. The current run is `pcr-20261015T082142Z-688a9d10`, conversation
`pcr-20261009T145235Z-e93791e3`, root
`/Users/mingliangxu/.codex_parallel_runs/20261015_162158`.

Read-only inspection identified failed candidates 2, 3, 4, 6, 7 and 8. Candidate
3 failed during remote compaction; all six share the terminal error
`workspace routing discovery timed out`, with `codexErrorInfo=other`. Earlier
native retries had reported `responseStreamDisconnected`, but that classification
was lost in the terminal error. All six durable journals were `blocked` with
zero PCR recoveries. Candidates 1 and 5 were still owned and running.

The actual scheduler is 0.1.86. The batch worker is PID 16315 and pinned to the
code loaded when it started. Publishing/staging 0.1.89 did not change it.
The already-paused client/scheduler deployment automation remains paused.

This is an upstream routing timeout plus a PCR recovery-classification defect.
The provider-side reason for routing discovery timing out is not established by
these logs. The runner must not turn that transient failure into abandonment.

## Root Changes

- Recognize the two observed terminal routing-timeout messages, including the
  compaction wrapper. Do not classify arbitrary timeouts, authentication,
  permission, invalid configuration or other application errors as recoverable.
- Reconsider an old misclassified blocked receipt only with its original
  command/prompt/home identity, accepted thread/turn/client-message identities,
  dead native owner, exclusive execution lock and no stop intent. Reconcile
  native receipts before sending the recovery continuation, never the original
  expensive prompt again.
- Add an explicit, fingerprint-bound in-place recovery path. It preserves the
  original run, private workspaces, private history databases, commands,
  instructions and append-only stdout/stderr. It does not use ordinary retry,
  which intentionally rebuilds an isolated candidate workspace.
- Handoff is allowed only after the original batch, including synthesis, has
  durably become idle. The old worker acknowledges its own retirement before a
  new worker is spawned. An active batch, pending operation, stop, rejected
  candidate, later workspace request or changed receipt blocks the handoff.
- The scoped maintenance controller skips global cleanup. Its recovery affects
  only the named candidates; normal synthesis refresh uses the original batch
  configuration after those candidates finish.

`scripts/resume_interrupted_agents.py` is read-only by default. `--execute`
requires the independently verified 0.1.90 runtime and refuses a busy batch
without constructing a mutating controller. It does not restart pcrd or macOS.

## Verification Boundary

New regressions initially reproduced the missing routing classification and
the refusal to resume its old blocked receipt. A real detached fixture also
exposed a relative import in the embedded adapter; it was fixed before acceptance.
The test now starts a real replacement worker and native protocol fixture,
resumes both original private threads, and verifies one continuation per thread,
unchanged workspace/database/rollout inodes and bytes, retained logs and actual
final answers. No real research task is used for QA.

The loopback native CLI fixture injects the captured terminal `other` metadata
after a real native synthetic tool call and a loopback provider failure. The
injection is explicit in its records; no real provider routing outage is induced.
It verifies that this exact terminal error triggers recovery in the same native
server, with retained tool output and exactly one synthetic job execution.
The installed old 0.1.89 adapter fails this case. An earlier notification-only
fixture also passed the old adapter and was rejected as inadequate; it remains
in `native-notification-only-fixture.log`, not the acceptance gate. Existing overload,
lost receipt and background-terminal cases remain enabled. Full source, installed
runtime, immutable release and actual recovery results are recorded below when
their gates finish; a passing fixture is not a resumed production Agent.

An anonymous engineering consultation was sent once to the existing embedded
browser discussion. It again returned `无法加载此回复`; no unreadable response is
treated as review or evidence.

## Current Production Boundary

No production Agent has been retried, stopped, re-prepared or resumed by the
investigation so far. All six failed candidates' original receipts and private
histories passed read-only identity preflight. The user has been asked whether
to preserve candidates 1/5 until they finish or permit a separately verified
safe interruption. No permission to interrupt those two is inferred from a
preselected option. No old deployment automation is resumed.

## Published 0.1.90 Boundary

The immutable 0.1.90 wheel was published and independently installed, not
activated as the scheduler. SHA-256:
`2eb25d5172bba1edb96d03d0725bccee635f09676be7aea66cd859119a221fcf`.
Its frozen source passed 1121 tests and 241 subtests; the installed runtime
passed 1120 tests (one source-package-only exclusion) and 241 subtests. Source
and installed native loopback each passed four cases. All production recovery
preflight was read-only; neither publication nor those tests resumed an Agent.

## Authorized Scoped Pause And 0.1.91

The user subsequently authorized: `核验安全后，允许该批次暂停并原地恢复`.
This is conditional authority for this batch's dialogue processes only, not a
global service/client restart or an override of a later user Stop.

The old busy worker cannot implement a new maintenance protocol. Sending it
SIGTERM would create user-stop files, so that is deliberately not the migration
path. The tested helper first freezes only the native dialogue servers, drains
already-emitted events, checks for active tools/terminals and independent remote
jobs, then freezes their old supervisor. The adapters close their own native
transport gracefully. Only after all original dialogue owners are gone and
history/jobs are verified does the helper retire the frozen supervisor. No
user stop file is created, cleared or reinterpreted. An unsuccessful safety
audit thaws the original processes instead of forcing the interruption.

0.1.91 extends retained recovery to a genuinely dead busy supervisor and an
accepted execution whose old supervisor never wrote `status.json`. This still
requires the original private receipt, identity, history, dead native PID and
exclusive execution lease. All unfinished candidates must be included. An
exact old operation/request binding, no newer workspace request, no rejected
candidate, no queued operation and no user stop are mandatory. The original
in-flight worker request is archived, never replayed. Busy state is published
only after replacement spawn has produced a real PID. The original scheduler
must first observe the old owner's actual exit, clearing its cached `Popen`;
no live code replacement or fabricated idle acknowledgement is used.

The POSIX helper's executable fixtures verify both audit rejection/thaw and a
successful drain with detached computation surviving. A real native CLI
loopback additionally cancels an accepted execution after its completed tool,
closes that server, and restores the same private thread in a new server. It
returns the final answer with the completed tool output and exactly one job
execution, without replaying the original input. This fifth native case is
separate from the explicitly injected routing-timeout fixture.

Source 0.1.91 passed 1126 tests and 257 subtests. Its wheel is immutable:
`07ef9f8d23361d5a3cb810f0872ed81cf6ad5b23d2eeef300a273bdb85e578c5`.
Installed/native/publication and actual production recovery evidence will be
recorded only after their respective gates complete. At this entry no
production process has yet received a signal and the scheduler is still 0.1.86.

## Publication Race Found Before Production Migration

0.1.91 remains an unactivated, unpublished candidate. Its source and installed
tests passed, but a stronger delayed-commit test found that its replacement
worker could emit events before its new owner PID was durably committed. An
already-running old scheduler could then overwrite the handoff with an
interrupted state. The new regression fails against the immutable installed
0.1.91 runtime (`checkpoint pid 29482 != original 999999999`). Neither that
fixture nor the candidate was applied to the real batch.

0.1.92 adds a narrowly scoped startup publication gate for retained recovery.
The new process exists, but it cannot emit worker events, open private history
or submit a continuation until `runs.json` durably names its real PID and exact
operation. User Stop or a publisher that died before commit prevents startup.
Ordinary worker startup is unchanged. The real detached integration delays the
commit and verifies that both the old checkpoint and private execution remain
untouched during that interval, before recovering both original private threads.

## 0.1.92 Release And Actual Batch Recovery

The final immutable wheel SHA-256 is
`e17a8cf07d65cecb231d42163cfba731a6b0b51bad336b53255bb6123e823762`.
Source passed 1127 tests and 259 subtests; the installed runtime passed 1126
tests (one source-package-only exclusion) and 259 subtests. Source and installed
native loopback each passed all five cases. Wheel/source and installed byte
verification and artifact-manifest hashes passed. 0.1.92 is now published in
the normal local release feed; 0.1.91 was never published or activated.

The first production safety attempt encountered an active tool and thawed the
native processes without draining an adapter or retiring the supervisor. A
subsequent read-only remote check timed out, before any signal. Neither case
was treated as task failure or permission to resubmit. At a later verified
safe boundary, both original dialogue owners drained, nine detached remote
compute jobs retained their original PIDs/start times, and the old supervisor
retired. Original private database/workspace inodes, rollout prefixes, commands,
instructions and append-only log prefixes were verified. No user stop flag was
created or cleared, and no workspace/history was rebuilt.

The actual replacement is PID `39696`, running the immutable 0.1.92 runtime,
operation `pcr-20261015T082142Z-688a9d10-01792074408625283000-853766d7ff`.
The original run/conversation, 8+3 configuration, prompt and submission-command
identity are unchanged. All eight private threads retained their original ID
`01a1212b-c09e-7ff0-9cfe-57df65f924f6`, each under its separate original home.
All eight have accepted new internal continuation turn IDs, not a replay of
the original expensive user input. Actual native adapter processes were checked
to contain the new routing recovery code; this is not just a queued request.
The authoritative old scheduler reports this same run as `running` with an
active worker after handoff.

The global scheduler remains 0.1.86, PID 56812 with its original start time.
The actual client is 0.1.436, PID 4569, started before this scoped migration;
this work did not install or restart it. The old global deployment automation
`pcr-0-1-88-430` remains PAUSED. No other batch or user process was stopped.

Evidence is in
`~/Library/Caches/ParallelCodexMaintenance/BatchPublication-20261015/`:
`publication.json`, source/installed test and native evidence,
`pause-before.json`, `pause-completed.json`, `remote-after-drain.json`,
`recovery-submission.json` and `production-verification.json`.
The research itself is still running, not declared completed by this repair.

Final production acceptance did not count `contextCompaction` or a changed
button/status alone as resumed work. Candidate 3 initially had only an accepted
continuation and context compaction, so acceptance remained incomplete. After
its compaction completed and fresh execution events appeared, the stronger
read-only verification returned success for all eight candidates, preserving
their original private history prefixes and identities. The old scheduler
still reported the same run as running. Detailed per-candidate continuation
turn IDs and fresh event types are retained in `production-verification.json`.

## Deferred Global Activation Safety Check, 2026-10-16 02:12 UTC

The user subsequently resumed the existing automation with global pcrd 0.1.92
as its target. The old paused-automation statement above describes the earlier
batch migration, not the current follow-up authorization.

The first idle preflight read all 348 runs through the enabled 0.1.86 IPC
client, as well as health, controller pairing and follow-up queues. No active
run, operation, admission or delivery was reported. All 16 follow-up queues
were empty. The formal desktop state had no outbox, accepted handoff or queued
item, and the actual open-window-ID list was empty. The command journal had no
unsettled record. GPT-6.1 Sol was present. This establishes initial idle, not
an atomic maintenance boundary or permission to skip subsequent safety gates.

The immutable published 0.1.92 wheel hash, all 337 installed package files,
source/installed JUnit hashes, and both native-evidence log hashes were verified
again. The ten recorded native cases remained valid. No wheel, installed
runtime, service definition or production task was modified by these checks.

The old activation sequence's last idle read and `launchctl bootout` are not
an atomic operation. The enabled 0.1.86 IPC server can still admit commands on
independent daemon threads and on the connected relay between those steps.
An isolated old-runtime IPC/new-runtime journal probe reproduced that boundary:
an admitted command interrupted before completion retained its original
identity but became `in_doubt`, with no entry in the new pending delivery index.
That is not sufficient evidence of automatic, lossless handoff. Separate cases
verified completed late receipts and timed-out receipts do not repeat effects.
All three probe tests passed; they demonstrate why the production activation
gate must remain closed, not that a production restart is now safe.

Consequently no global activation command, service rewrite, production signal,
client restart or user-task submission was performed. The actual global daemon
remains healthy at 0.1.86. The follow-up remains active, waiting for a verified
atomic ingress/drain boundary; repeated idle snapshots alone must not bypass
it. Production SIGSTOP/SIGKILL or a private-runtime rewrite is not a substitute.

The reproducible probe is `test_global92_handoff.py` in the existing maintenance
evidence directory. `global-activation-preflight-20261016.json` records its hash,
the read-only checks and the deferred decision. An embedded-browser technical
consultation was unavailable (`net::ERR_CONNECTION_RESET`); no webpage response
was treated as evidence or as authorization to restart.

## Global 0.1.92 Activation, 2026-10-16 04:17 UTC

The user explicitly instructed: "不需要做这个啊。现在直接更新。"
This supersedes waiting for a new atomic ingress mechanism. No such mechanism
was implemented or claimed as part of this deployment.

The direct-update entry verified the immutable wheel, installed package bytes,
existing source/installed test and native evidence, client compatibility,
model availability, pairing, and current idle/message state. It preserved the
original LaunchAgent configuration except for the runtime interpreter, then
reloaded only that LaunchAgent. The actual global scheduler changed from
0.1.86, PID 56812, to 0.1.92, PID 73487.

Live IPC confirmed 0.1.92, healthy PCR, connected relay, retained controller
pairing and GPT-6.1 Sol. The command-receipt store and paired-machine identities
were unchanged. All 35 baseline client, Node and retained-worker process
identities were preserved. The client executable hash, inode and Info.plist
were unchanged; no client was installed or restarted. No user task was stopped,
adopted, resumed or resubmitted by the deployment. Active runs were empty.

Evidence: `activate-direct-20261016.py`, `pcrd-before-direct-20261016.plist`
and `activation-direct-20261016.json` in the existing maintenance directory.
After successful activation, the existing `pcr-0-1-88-430` follow-up was paused
through the app automation tool so it will not keep polling or restart again.
