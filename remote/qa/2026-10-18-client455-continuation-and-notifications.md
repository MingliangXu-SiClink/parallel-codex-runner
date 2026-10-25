# Continuation Projection and Completion Notifications

## Scope

macOS 0.1.455 and pcrd 0.1.100 address follow-up/Agent-switch coherence and
execution-completion notification eligibility. Execution-configuration visuals,
research directories, computation policy, message identities and adoption
commands are unchanged. This is not a competition restart or result adoption.

Private evidence is in
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/Continuation455-20261018/`.

## Reproduced Projection Failures

Isolated native/Unix-loopback regressions reproduced these distinct failures:

1. Installing an accepted continuation discarded its already prepared transcript
   dictionary. The answer disappeared even though the exact destination cache
   was available. Activation now restores/merges that cache, history and Agent
   selection before publishing the destination, rather than clearing its body.
2. A foreground `turns.get` compared its response with the Run captured before
   `await`. A newer visible/background result arriving during that wait could
   then be replaced by the late running summary. Commits now compare against
   the latest matching revision; index updates use the same ordering rule.
3. Recovered handoff history came from the current global view rather than the
   command's original Run/Agent. Recovery now hydrates that exact source branch,
   also filling in an incomplete optimistic history without replaying input.
   An optimistic question is not attached to another Agent's answer while that
   other branch is being read; the durable command/confirmation remains intact.
4. If the continuation was already in the index before the old source view was
   restored, the old `previousLatest == visible` condition never caught up.
   A latest-following view now installs the indexed destination and its cached
   body together. Only an explicit, scoped historical navigation stays pinned.
   An index mismatch by itself is no longer interpreted as historical intent.

Real historical navigation, direct native-session history and uncached paging
remain supported. No footer was merely hidden. The regressions identify concrete
causes; they do not establish the exact callback order of every past screenshot.

## Notification Cause

Both daemon monitors previously equated terminal Agent states with execution
completion. An adoption/stop transition with success/killed Agents therefore
passed the same predicate as a naturally finished round. Before the fix, all
15 adoption/stop/discard subcases in the new monitor tests incorrectly delivered.

The monitors now require an execution-completion Run state, no active worker,
all reported Agents terminal, and no finalization/discard identity or operation.
They do not require every Agent to have succeeded. Empty/preparing/pending
synthesis is not all-Agent completion. Suppressed terminal watches are consumed
without delivery, including across restart and failed-notification retries.

An additive persisted `completion_reason` distinguishes execution from adoption,
discard, cancellation and finalization recovery. In particular, a failed or
reversed adoption returning to `completed` is not a new execution-completion
event. Existing state envelopes and receipt identities are retained. The client
uses the same cause policy; transcript/summary readers no longer dispatch OS
notifications. App fallback notification requires an observed working transition,
not an initial historical read. Permissions, foreground suppression and existing
notification deduplication remain unchanged.

## Backend Verification and Activation

- Source tests: 1,209 passed and 304 subtests passed. Installed 100 runtime:
  1,208 passed and 304 subtests passed; only the source-tree vendored-location
  assertion was deselected for the installed-package run.
- Immutable wheel: `parallel_codex_runner-0.1.100-py3-none-any.whl`, 1,172,405
  bytes, SHA-256 `b3b1066da0216d94d7831019f81f5ca6f1476cda67db0945c2eee7f7ffbb91aa`.
  Only `plugin/state.py`, `plugin_runtime.py` and `remote/notifications.py`
  differ from the 99 wheel. All installed module bytes match the 100 wheel;
  pinned dependencies and `pip check` pass.
- Nine unsafe management-boundary fixtures were rejected. After fresh admission,
  delivery, queue, actual-window, draft/handoff and question-answer checks under
  the maintenance lock, only the management service switched at 09:56 UTC.
  Old manager PID 55700 exited before new PID 55480 started. LaunchAgent,
  capabilities and loaded runtime module paths agree on 100.
- All eight original detached owners retained PID/start-time/runtime identity.
  The original receipt store, pairing, client process, loaded client bytes and
  persisted drafts/message/queue/question state were retained. No owner, input,
  task, remote computation or client window was restarted or replaced.
- Both Agents active at the activation boundary retained their original native
  thread, turn, command, workspace and receipt identity, and produced subsequent
  native events after the new manager was online. One completed normally;
  the other continued running. This is native continuity evidence, not merely
  `running` or a live PID. It is not a full audit of all competition compute jobs.
- Local health and Relay state were checked separately. The existing paired
  manager reconnected normally at `2026-10-18T09:56:31.525461Z`; no manual token
  refresh, command replay or additional bridge consumer was created.

## macOS Delivery

Final frozen-source verification passed 611 native tests in 58 suites
(332.611 seconds), plus 86 focused tests in six suites under the strict
cooperative pool (18.457 seconds). Coverage includes acceptance-time branch
switches, prepared destination restoration, cold persisted handoffs, incomplete
optimistic history, late summary/history replies, an already-indexed latest
turn, explicit historical navigation, drafts, idempotent delivery and lifecycle.
The native offscreen timeline retained both the original answer and the new
answer through repeated Agent switches; no QA window was presented or focused.
Protocol/desktop-state, icon, offline signing-store and signing-disabled gates
also passed against the same frozen inputs. Interactive-only suites were not run.

An additional strict-pool run including the older blocking RunAvailability test
fixture stalled and was not counted as passing. Only its isolated test helper
was terminated. RunAvailability is included in the passing regular full suite;
the final strict scope is explicitly the six suites above, not the entire suite.

The signed immutable macOS archive was published to the actual authenticated
Relay catalog at `2026-10-18T10:19:56.332688Z`, release ID
`macos-release-86b2f05a21bb4756bfb029e2e81cc981`. The 14,813,473-byte archive has
SHA-256 `35105fcefc76cada00cec0899eabac4b76d483fd2243f96e7960fcfb01b1ca9d`.
It retains the existing `Parallel Codex Release Signing` publisher. Native deep
strict signature, exact archive/file and resource-rendering checks pass. The
release manifest includes both macOS 455 and the independently verified pcrd 100
wheel; immutable older artifacts remain untouched.

The actual installed 454 controller reports 455 available. A supported
authenticated ticket download matched the exact published hash/size, without
persisting the ticket. The unchanged installed 454 native updater accepted it
in prepare-only mode, and all staged file bytes matched the signed bundle.
No client installation or restart was attempted. Final observation still finds
454/PID 42872 with its original loaded bytes and start time, and pcrd 100/PID
55480. The one actual open window's persisted draft/outbox/handoff/queue and
selection hashes are unchanged. Local health is OK; the Relay remains connected
and authenticated delivery succeeded through it. Older idle Runs missing from
the bounded `runs.list` projection were read back by ID, not declared failed or
revived merely because of that list's coverage.

Thus the backend policy is enabled and the desktop repair is formally available
and verified for installation, not already installed or proven on the live
production 455 UI. The native regression images are isolated fixture evidence.
No trust/Keychain/update-verification setting was changed, no research data was
rolled back, and no real prompt, adoption, stop or replay command was issued.
