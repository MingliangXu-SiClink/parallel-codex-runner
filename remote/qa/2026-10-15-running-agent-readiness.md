# Running Agent Readiness: macOS 436 And pcrd 0.1.89

## Observed Baseline And Scope

The user reported long empty intervals when switching running Agents, not just
completed-answer navigation. The actually installed and loaded client was
0.1.435/build 435, PID 1028, executable and loaded inode 184448994. The actual
paired daemon was 0.1.86. This was not dismissed as an uninstalled update.
The user-paused `pcr-0-1-88-430` deployment automation remains PAUSED.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/RunningAgentReadiness-20261015`.

No real user turn, retry, stop, adoption, discard, research-file edit, foreground
QA window, client/service restart or update-automation resumption is performed
by this work. Building, publishing and staging an isolated runtime do not mean
installation into the live scheduler or an installed-client acceptance test.

## Confirmed Gaps

1. The independent workspace reader explicitly excluded the selected workspace.
   That workspace still used the older navigation-bound, serial Agent preloader.
   One slow branch could delay all later branches; switching pages ended that
   reader. The previous completion-oriented warm-cache tests did not cover this
   running-workspace startup path.
2. All four background transport slots were shared by metadata, history and
   live output. Slow unrelated observations could prevent ready-to-run transcript
   reads from starting. The running-client sample captures all four background
   slots waiting on socket reads, while the main thread is mostly idle.
3. pcrd rewrote each output fragment by calling `rewrite_agent_visible_text`.
   Each call reacquired the manager lock and invoked `_require_run_locked`, which
   rereads/decodes the global run state. A page with 256 output fragments caused
   256 such lookups merely for display-path rewriting. The installed 0.1.86 and
   staged 0.1.88 source both contained this loop. The current state file was
   about 15 MiB and the inspected running event log about 89 MiB.

Two bounded live read probes (`turns.get` and `runs.agent`) each timed out after
120 seconds; this is evidence of unavailable reads, not evidence that user work
failed. The daemon was neither restarted nor modified. Native process samples
show manager-lock contention and JSON decoding but do not assign every second
of the production delay to a single function. The deterministic 256-fragment
regression independently proves the unnecessary read amplification.

A further regression exposed a fourth gap: a partial foreground snapshot could
replace the background snapshot's entire Agent dictionary. Branches already
prepared offscreen disappeared from the shared store, and the reader's already
attempted revision meant they were not automatically reread. A foreground save
could also republish older Run metadata. Snapshot saving now preserves other
prepared branches, contiguous work and same-artifact answer cursors; it does
not regress the newer known Run summary. Selection can install the exact missing
branch from that prepared memory snapshot without starting another read.

A fifth gap was treating a running branch's current EOF as final until the
metadata revision advanced. Live output now has its own periodic cursor probe.
The regression holds the metadata slots, freezes the Run summary and advances
all eight real output streams; it checks that the bodies still catch up.

The strict-pool run also exposed a missing `SO_NOSIGPIPE` option on the real
client socket. A server closing during request writing could terminate the
client instead of returning a connection error. `peer-close-red.log` reproduces
signal 13 with a disposable local peer; the socket now suppresses that signal
locally and preserves the existing transport error/retry/command-ID semantics.
No process-wide signal handler or success masking is introduced.

## Root Changes

- Use the same machine/workspace/run readers for visible and nonvisible
  workspaces. Their lifetime no longer follows workspace or Agent navigation.
  Live readers observe new metadata and newly created synthesis branches.
- Each branch reads independently, with the existing aggregate four-request
  background budget split into two live-content and two other observation slots.
  Commands are not put in either observation queue. Local and forwarded reads
  are classified by their actual inner action.
- Coordinate foreground work reads with the exact shared branch reader instead
  of duplicating that reader. Answer-only reads remain independent so a saved
  terminal answer does not wait behind held work pagination.
- Retry failed reads in the background with scoped ownership and backoff. A
  failed completed read still finishes its current attempt; a separately owned
  delayed retry is cancellable on window shutdown or workspace removal. A new
  workspace status hint starts the existing metadata poller rather than waiting
  for a page to be reopened.
- An EOF observed while an Agent is running is not permanent. Live branches
  probe their current output cursor every three seconds through the reserved
  content slots, even when metadata is late or its request slots are held.
  Unchanged pages do not regenerate/publish their presentation. A separate test
  advances all eight branches with the visible Run summary deliberately frozen
  at its older event count and all other observation slots occupied.
- Resolve the accepted workspace mapping once per output page, then rewrite all
  fragments against that consistent mapping outside the manager lock. Original
  fragment bytes, event IDs, page order and cursors remain intact except for the
  pre-existing accepted-workspace path transformation. No history, execution,
  worker pinning, finalization or storage-protection code is changed.

## Verification And Limits

`red-tests.log` records the three new running-reader and slot-starvation cases
failing on the prior client. `backend-red.log` records the page-level batching
case failing before the backend change. Failed/interrupted intermediate test
runs are retained; only final frozen-source runs count as acceptance evidence.

The new isolated socket cases use running Agents with real output items and no
saved final-answer artifact. They cover the initially selected workspace, one
held branch, all non-content slots held, background output advancement, rapid
native branch switching, leaving/reentering a running workspace, and synthesis
branches appearing without an Agent click. They assert exact rendered output,
branch identity and zero mutation actions, not just a changed selection marker.
They also cover a partial visible page saving over a complete prepared snapshot
and verify that selecting a retained branch performs no Agent RPC. Observation
tests now wait for readable snapshots rather than the end of a persistent live
reader; their assertions against repeated history reads and stale-page overwrites
remain in place.

The old Agent-preload fake server itself performed blocking poll/read/write on
the cooperative executor. Those fixture operations now use the existing GCD
bridge, and the accept task owns its listener close until it exits. This avoids
strict-pool starvation and descriptor reuse between fixtures; no test was
removed from the strict regression set. Intermediate SIGPIPE and fixture
failures are retained separately from the final acceptance logs.

The native timeline windows remained unshown and non-key. Final frozen-source
verification passed:

- Backend source: 1106 tests and 216 subtests.
- Isolated installed 0.1.89 runtime: 1105 tests and 216 subtests, one
  source-package-only case deselected; installed wheel bytes verified again.
- macOS: 524 tests in 54 suites, plus protocol, desktop-state and icon checks.
- Strict cooperative pool: 105 tests in nine suites, including all Agent-preload
  cases, late metadata, message identity/restart and peer-closing regressions.
- Running native body appearance: 24 switches over eight branches with no saved
  final artifact; maximum 26.264 ms in the normal run and 54.363 ms in the strict
  run. Text and PNG checks confirmed the actual Agent body, not just the tab.
- Existing completed-answer stress: 50 workspace switches and 400 branch
  switches with disk and health held; normal-run p95 1.534 ms, max 1.660 ms.

`client-final-tests.log` and `strict-tests.log` are the final acceptance logs.
`source-hashes-before-tests.txt`, the after-test/build copies and the publication
checks agree; no client input changed after final testing. These are controlled
local native/socket tests, not a claim that an installed production 436 client
has already passed live acceptance.

The existing embedded-browser discussion was revisited with the new running
case. The website again returned `无法加载此回复`; no readable partner review or
agreement is claimed. No research content or credentials were sent.

Hot switching means selecting an already maintained readable snapshot. First
connection, content not yet emitted by an Agent, a cold/evicted record, and
unavailable service reads are not misrepresented as fully synchronized. Cached
reading does not relax message, workspace, branch, revision or action checks.

## Publication And Activation

Published on 2026-10-15. The signed macOS 0.1.436/build 436 archive is in the
formal Relay update catalog, release
`macos-release-e8fd50e0202945d7ad3ac49165309532`. The paired controller's
authenticated built-in updater check confirms this exact build and SHA-256:

`d93bad8a0e529d27db3e06b7e0b84887c9d55be69915be17aee384d1552dee8f`.

Signing identity and designated requirement match the existing official client.
Deep/strict signature validation, archive extraction, version and bundled
rendering resources passed. The executable SHA-256 is
`c5cf9448391bf5e0d30f6b8c30e6497c9aaa2bf4753144966330db81e1133522`.

pcrd 0.1.89 is published in the local release manifest and installed only in
its independent runtime. Its immutable wheel SHA-256 is
`630ae9a696bc2055d82a54357135869df48935d072f79d5202caf92f7de4f431`.
Compared with the immutable 0.1.88 wheel, only the two display-rewrite modules
changed; AST checks exclude changes outside their three intended methods.
Original release artifacts remain intact; Android and Relay versions did not
change.

Post-publication readback still shows the official loaded client 0.1.435,
PID 1028, inode 184448994, and paired pcrd 0.1.86. Before/after publication,
all 47 observed Node process identities, accepted handoff identities and the
empty outbox were unchanged. The deployment automation remains PAUSED.
No installation, scheduler activation, restart or new deployment automation
was performed. Publication/staging is not described as deployed improvement.
The existing paused automation must not be repurposed to activate different
versions or bypass its original byte and idle gates.
