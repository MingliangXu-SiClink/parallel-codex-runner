# Interactive performance, 2026-10-28

## Acceptance and scope

The user's limits remain: real response to a new/follow-up message within 10 s;
history plus current Agent conversation within 1 s of workspace/Agent selection.
An acknowledgement, old cache, spinner, or partial history does not pass.
No Goal mode, scheduled task, synthetic competition prompt, input replay, or
interruption of healthy Agents is authorized. All work uses the desktop PCR repo.

## Mechanisms and changes

- Navigation previously reused utility reads without promoting the newly selected
  destination. Focus now promotes the existing queued request, not another RPC.
- Current-body, work backfill, metadata, history, and write delivery have separate
  ownership. One shared work stream distributes canonical event ordinals to the
  original Agents; filtered body pages do not wait for Agent lifecycle/artifacts.
- Validated, disposable conversation indexes reuse canonical JSONL positions.
  They retain contiguous coverage and do not advance work or receipt cursors.
- A cold navigation could lose its destination when an invalid early Agent click
  cleared the workspace-navigation intent. Validation now precedes that change.
- Even after 119, legacy worker append locks delayed reads: paired memory-run
  reads took 2.047 s (`runs.get`) and 1.716 s (`runs.events`), with 34/180 lock probes
  busy. Reconciliation logs showed up to 6.124 s waiting with little thread CPU.
- 120 readers capture a complete published prefix without waiting for an active
  legacy appender. Existing writers publish unbuffered JSONL before offsets and
  repair only an unpublished tail. Inode/length checks reject source replacement.
  Authoritative count/command locks are unchanged; this is not a cached status.
  Expensive new-event formatting is moved outside the append critical section.
- Lightweight liveness is separate from aggregate status. Slow history reads
  must not be misreported as transport loss. LaunchAgent scheduling is Interactive.

## Evidence, not blanket acceptance

Private evidence directory:
`~/Library/Caches/ParallelCodexMaintenance/InteractivePerformance-20261027-39zHuL`.

- Exact legacy 111/112/113/114 writer modules were exercised only in temporary
  fixtures. While their actual append code held its lock, new readers returned
  the original prefix in 0.0021-0.0764 s, then the new original ordinal after release.
- Prefix regressions cover partial JSONL, complete unpublished records, partial
  offsets, writer release/crash repair, source deletion, and formatting contention.
  Focused backend tests: 292 passed, 61 subtests passed.
- Offscreen native cold navigation rendered complete fixture history and current
  body while metadata/full-work reads were deliberately held: 0.315 s including
  release verification. No visible QA window, focus change, or business command.
- Earlier isolated 40-branch source-new navigation maximum: 0.542 s. This is not
  proof of the same bound for every production conversation.
- 119 production RPC bodies: 0.003-0.528 s, but cold historical reads still
  0.668-1.362 s. An earlier fully cold scan took 77.156 s. Warm measurements alone
  cannot establish the 1 s requirement.
- The actual 10 s submit-to-native-output path remains unverified. Older receipts
  showed 75-155 s preparation and 7-28 s accepted-to-output; an accepted receipt
  is not a real response. No production message was sent to manufacture a pass.

## Delivery and safety

At this audit's source checkpoint: installed macOS 481 and pcrd 119; 482/120 are
source candidates, not yet release/installation evidence. Subsequent immutable
artifact, signed publication, download, install, and PID/hash records belong in
the private directory; do not infer installation from a manifest.

At 04:13 local: all 11 original detached workers remained, with 11 live native
processes. Five competition heads contained 34 physical Agents. User-stopped
memory synthesis 10/11 remained killed. The existing pending kill receipt hash
was unchanged; no follow-up/admission/delivery item was outstanding. Recheck this
before any daemon switch. No Relay, WSL, or remote research job was restarted.

Only exact generated staging trees from this repair were cleaned; approximately
1.42 GiB logical size. Retain runtimes pinned by live workers, original receipts,
research files, and rollback program bytes. Never roll back accepted user data.

## Installed 483 / 121 checkpoint

- A real-data offscreen native navigation probe exposed another independent
  main-thread bottleneck: at 12,000 layout-cache entries, every newly measured
  row sorted the entire cache to evict one entry. Native rendering alone took
  1.124-3.107 s. Eviction now reserves one-eighth of capacity per trim; exact
  measurements and scroll anchoring remain unchanged. The saturated-cache
  insertion/persistence regression passed in 0.102 s.
- Darwin's five-connection default Unix-listener backlog refused bursts while
  the daemon stayed alive. An isolated authenticated 16-connection regression
  reproduced errno 61 before the change. The listener backlog is now 128;
  authorization and command identity checks are unchanged, with no write replay.
- Native history reads now persist a disposable raw conversation projection,
  validated against the original file identity, length and timestamps. Path
  display mappings are reapplied, not frozen into cached text. Corruption falls
  back to original records. Original history hashes and entry IDs matched.
  Paired original memory/compression histories: uncached 0.422/0.506 s,
  cached 0.0096/0.0135-0.0139 s. Neither source history was rewritten.
- Latest real-data native test: five workspace heads, all 34 branches, complete
  original historical ID sets plus the current canonical body cursor and native
  visible text checked. Maximum 0.862335 s. Of 163 proxy requests, there were no
  transport failures; three attempted selection writes were deliberately rejected
  by the read-only proxy and never reached production. This is not an observed
  click-to-paint measurement of every installed-client workspace or a universally
  cold-cache guarantee. Native layout uses the disposable shared metrics cache;
  model, outbox, queue and conversation state were isolated.
- Backend: 1,304 tests plus 342 subtests passed; installed wheel: 233 tests and
  323 Python-file hashes verified. Native: 728 tests in 65 suites passed, excluding
  focus-changing suites. Build requires the verified macOS 26.5 SDK; the first
  attempt with the ambient SDK failed on a missing SwiftUI macro plugin, produced
  no release, and was rebuilt with the explicit SDK without changing dependencies.
- A separate real-provider smoke used a temporary private home, an ephemeral
  synthetic thread, GPT-6.1 Sol / max, and no tools or competition message. First
  real output: new turn 4.925 s; follow-up 2.842 s. Initialization/account/thread
  setup took another 2.524 s. This does not prove production long-history,
  multi-Agent, or waiting-queue submission meets 10 s. Existing busy-run queue
  semantics and user-configured countdowns have not been silently changed.

Signed macOS 0.1.483 publication:
`macos-release-403693115a07444b878d49d6147f66ba`.
Archive SHA-256: `5bc57d208c8e337ae65e52fbe5ac30262a6d920e7b3f5150cef1d40ae60b3297`.
Installed executable SHA-256:
`7c59f858493ca67738f2d3fc77b5b54002fc62e25085aec406a6ad19a1b68aec`.
Existing authenticated Relay update check, ticket, download and native signature
verification all succeeded. Client PID 39210 exited normally; 54730 loaded the
new executable in the background. Actual window restored the original memory
conversation, historical navigation and current body; no reconnect banner.

pcrd 0.1.121 is loaded by LaunchAgent PID 50218; old PID 37079 exited normally.
All 11 detached workers retained their process/start identities; all 10 live
native channels retained thread/turn/message identities and produced subsequent
original events. Draft/outbox/accepted handoff/answer/queue hashes and user-stopped
Agents were preserved. No Relay, WSL or research job restart occurred.

Evidence: `live-navigation-121-483/`, `history-compare121-cache.json`,
`native-provider-response-smoke.json`, `native-483-regression-result.json`,
`publication-483.json`, `client483-installed-verification.json`,
`client483-actual.png`, and `live-native-after121.json` in the private directory.
The requested universal 1 s / 10 s acceptance remains open, not marked complete.

## Expanded navigation and 484 source correction

Expanding the original-history probe to all registered nonempty workspace heads
found a 1.054 s branch outside the five competitions. Sampling exposed ordinary
text allocating LaTeX delimiter slices per character; removing that allocation
reduced a 187,399-byte scan from 0.132 s to 0.008-0.010 s. Mixed math, escaped
currency, inline/fenced code and Unicode remain unchanged. The 50 ms regression
failed before this change and passed afterward.

That optimization alone was insufficient: the same real-data branch still took
1.402 s. The remaining native layout error was attaching a replacement document
before configuring its history-rail insets, then discarding the measurements it
had just made. AppKit typeset the whole history at a throwaway width and again at
the final width. Replacement now establishes the final reading column before
exposing its data source and retains those measurements. A focused native test
proved that the old code persisted measurements at the provisional width; the
new code does not. No history rows are omitted or replaced with estimated text.

After both changes, all 36 branches across seven readable workspace heads passed
the same history-ID/current-cursor/native-visible-text probe; the previously slow
branch took 0.939 s (native render 0.640 s). These are offscreen native timings,
not a universal installed-client guarantee. Source regression and signed 484
delivery are separate gates, recorded after this checkpoint.

Six other old workspace heads could not supply their original resume histories.
All six PCR records already say their private Codex homes were deleted after
finalization/discard; the current history API reports the referenced rollout
missing. They are explicitly excluded from passing results, not hidden by a
cache or silently presented as complete. No research project files were read,
restored or altered, and the cause/date of the global rollout loss is unproven.

Evidence: `live-navigation-121-483-all/`, `live-navigation-121-484-source-all/`,
and `live-navigation-121-484-geometry-all/` in the private evidence directory.

## 484 Installed, 485/122 Candidate Checkpoint

484 was officially published, authenticated-download verified, strictly signed,
and installed by normal client exit (PID 54730 to 62617). Its binary SHA-256 is
`01a924bc28b1a789df481fc8d57cc5a46034ff3f9ba4bceeec4a10ecd3b6c062`.
Eleven detached workers and ten native channels retained their original process
identities; subsequent native events were observed. This is installation and
non-disruption evidence, not acceptance of the user's universal latency limits.

Subsequent cold-render repetitions disproved a robust one-second result: the
227-entry head took 1.01-1.42 s, although the other readable branches were below
one second. Main-thread full-history Markdown/TextKit sizing dominated. One
request trace additionally placed the first history RPC about 0.9 s behind the
current-body RPC even though individual RPCs took only 5-15 ms. First-frame
layout and awaited UI callbacks were gating history pagination.

The candidate now starts sequential, bounded history I/O independently of
AppKit. A one-element `AsyncThrowingStream` buffers cumulative snapshots, so a
slow display coalesces obsolete intermediate paints without dropping original
entries or marking a partial page complete. Cache hydration and original
machine/workspace/run/token guards still precede publication. Tests cover a
blocked main thread, coalescing with all original IDs, cancellation, shared
foreground/background reads, and late replies after navigation. Native cold
layout is still above the limit in the latest probe (1.406 s); this change is
not reported as a complete SLA fix. A TextKit object-reuse experiment did not
solve it and was removed, rather than retained as speculative complexity.
Per-message Markdown base styles are reused; only syntax-free single paragraphs
bypass parsing, with equivalence checks against the existing rich-text path.
A late run read now publishes a newer known completion instead of leaving the
visible run working; it cannot rewind newer output or change a draft.

A separate real-provider follow-up probe found an execution-entry defect:
fresh CLI homes produce state migration 58 and projection migration 7, while
the scoped-history adapter recognized only newer reviewed schemas. This raised
`Unsupported Codex history schema or migration` and left continuation retryable.
Reviewed exact fingerprints are now accepted; unknown schemas/checksum changes
remain rejected, unrelated sessions are excluded, and the source DB is unchanged.
The native loopback test now resumes the fresh CLI schema before upgrading its
fixture, preventing the old test from hiding this case. Four original-thread
resumes and five loopback requests passed; 28 focused backend tests passed.

One valid isolated real-provider pair reached visible native text in 9.740 s
(new input) and 9.326 s (same-session follow-up), with both runs completed. Earlier
probe failures included a fixture that restored 4+2 settings, sync-back disabled,
and an overly exact punctuation oracle; these are not counted as passing pairs.
Only the valid pair is evidence, not a universal ten-second guarantee. The
real-provider queued-follow-up path and installed click-to-paint acceptance
remain unverified. No competition message was used as test input.

Evidence: `isolated-full-provider-485-validated/`,
`live-navigation-121-485-final-cold-all/`,
`live-navigation-121-485-independent-history-cold-all/`, and
`live-navigation-121-485-stream-history-cold-all/` in the private evidence root.
The Pro web consultation remained unavailable with `Unknown error`; no lower
mode or fabricated partner conclusion was substituted.

## 485 Additional Root-Cause Checks

A bounded live sample of client 484 (PID 62617) found its main thread mostly
idle, but the conversation-cache worker repeatedly decoded the unchanged
on-disk record before merging and re-encoding each update. A bounded decoded
record cache now reuses only an exact inode/size/mtime revision, validates the
key, detects external replacement/corruption/removal, and verifies the bytes
after its own atomic write before retaining the new revision. It changes no
persisted format or authoritative merge rules. Twelve repeated writes of a
340-KB history no longer decode their own unchanged record; a cold reader still
reads the complete persisted result. Cross-instance and same-size/mtime atomic
replacement tests pass.

Native Markdown now uses Foundation's NSAttributedString parser directly,
avoiding the Swift AttributedString rope-to-UTF16 round trip. The original
formatting, tables, lists, links, images, and font fallback remain covered by
structural-parity and rendering tests. The application entry point is separated
from the reusable SwiftPM library: optimized tests previously entered the app's
main loop instead of executing tests. The release test now genuinely executes.
It still failed the cold first-head limit (2.092 s, 1.123 s in native rendering);
the other readable branches passed. This is not a one-second acceptance claim.

The initial full candidate run failed three tests, including complete persisted
history causing an unnecessary RPC. Hydration now occurs in the independent
producer before any RPC; page publication retains original context/token checks.
Two test waits were corrected to await both independently published pieces of
state rather than assuming their order. The subsequent noninteractive full run
passed 739 tests in 66 suites (393.589 s). Backend 122 passed 1,307 tests with 342
subtests; the staged wheel passed 233 focused tests and 323 installed Python
source hashes. None of these constitutes the user's live latency acceptance.

During focused verification an overly broad direct Swift filter inadvertently
included foreground interaction suites. The owned test process was interrupted;
no PCR client or Agent was stopped. Subsequent runs used exact test names or the
repository's foreground-excluding runner. Do not report that interrupted run as
noninteractive or passing. No production message was submitted as a test.

## 122 and 485 Activated; Remaining Queue Wait

The two optimized, unsampled cold-render repetitions passed all 36 readable
branches across seven workspace heads; maxima were 0.495 s and 0.625 s. They use
real read-only daemon data and native collection/text rendering, with every
historical ID, current cursor and final visible marker checked. They are not
installed mouse-click measurements, and source history was warmed while
collecting the expected IDs. Six missing-rollout heads remain explicitly
unavailable, not passes. The earlier sampled/debug cold failures above remain
part of the evidence.

pcrd 122 was activated with PID 3487 and wheel SHA-256
`7427ac7fffcdf17439053e382b15e3bcb6263fc3ec0d48012bb037894011cbe2`.
Eleven detached worker identities were preserved. Original native channels
continued producing events; the remaining candidates later completed naturally.
Their completion is not attributed to the update. The memory run's three
syntheses subsequently failed before native preparation in their retained older
worker with the history-schema guard. Their empty private homes, absent branches
and empty commands prove no native submission occurred. The existing retained
preparation-recovery guard accepted a read-only plan; no recovery was executed.
Its idle owner and all candidate results remain intact. The recovery tests pass
(21); it must not be replaced by ordinary workspace-rebuilding retry.

macOS 485 was published, authenticated-download verified, strictly signed and
normally restarted (62617 -> 8059), binary SHA-256
`32784e04552c17d0e9bafd076dc04472e384bcda0d36e584155572be094f48c5`.
Drafts/outbox/answers/queues, eleven workers, original pending command and user
stops match pre-installation. The actual current window retains all 50 original
history markers, current answer, work disclosures and inline image rendering.
Native channels were already complete before client restart. The first build
attempt used SDK 27 and failed at SwiftUI macros; the delivered artifact was
rebuilt using the validated SDK 26.5, not that failed output.

The authenticated download initially exposed a real public ingress outage;
see `2026-10-28-nginx-restart-race.md`. After that repair, the existing authenticated
catalog, ticket, archive hash and native verifier all passed. Local health alone
was never used as evidence of external connectivity.

At 08:03 CST the selected account reported its weekly allowance 100% used.
Additional real-provider ten-second trials and the original unsubmitted
synthesis recovery are therefore deferred, not claimed successful. The user
was asked to choose an account with available allowance; none was changed and
no credits were purchased. Offline/native and loopback validation continued.

One additional deterministic queue delay was reproduced: after resolving and
durably rebinding a queued workspace/conversation to a concrete run, the client
still slept for its full two-second polling interval. A regression timed out
at 1.018 s on the old code. The loop now immediately checks a changed/consumed
head through the same stop, pause, retry, identity and countdown guards. An
unchanged waiting head retains its bounded polling interval; configured queue
delays and busy-Agent ordering are not bypassed. The same original command
reached dispatch in 0.015 s; 20 focused queue/native-response tests passed.
Loopback queued submission-to-visible-answer was 2.820 s including queue residence.
This change is a 486 candidate until its separate full-test/release/install gates
are recorded. It is not evidence of a real-provider queued ten-second guarantee.

## 486 Released and Installed

Native regression: 740 tests in 66 suites passed in 388.638 s, with foreground
interaction suites excluded. The final read-only native navigation trial covered
42 branches across seven readable workspace heads: 0.013-0.301 s from selection
through complete original history/current-body rendering. It used release code,
an empty client render cache, no sampling, and exact original history IDs,
cursors and visible text checks. The source history was warmed during oracle
collection; this is not an installed mouse-click measurement. Six older heads
with missing native rollouts remain unavailable, not passing cases. Mutation
attempts such as `followups.select_agent` were rejected by the read-only gateway.

Release `macos-release-025d6116a62e43dea0d6c9608d8ae22d` was published through the
existing Relay and authenticated catalog/ticket/download path. Archive SHA-256:
`eaf5f548e607fbf3116d777e1529151cf1c90f33586109bf8b70ce1d2eb0c781`.
Strict signature and packaged rendering checks passed. Normal background
installation replaced client PID 8059 with 11990; loaded binary SHA-256:
`bd61de6b566315a6db6273615f45be35cdf5ff46b3cd94e2a9e9baaa56458852`.
The pcrd 122 PID 3487, all eleven detached workers, pending stop receipt,
user-stopped Agents, questions, drafts, outbox and queues were unchanged. No
native channel was active at this client restart; do not call that resumed work.

The actual restored window was checked without clicking or taking focus. It
retains all 50 exact history-marker labels, the empty composer, current answer,
inline image and work disclosures. The screenshot and structured comparison are
in `client486-actual-window.png` and `client486-ui-verification.json` under the
private evidence directory. The first AX counting helper treated multiline
labels as subtree endings; the corrected helper counts numbered AX button
nodes. No result depends on that erroneous one-node count.

Account allowance still displays zero. No additional real-provider request,
account change, credit purchase or synthesis recovery was performed. The user's
universal one-second/ten-second acceptance remains **not established**; release
and installation are not a completion claim.

Owned inactive build intermediates were checked for open handles and removed:
the repository's failed SDK-27 `.build/out`, `source484`, and the failed
`source122/apps/macos/.build/out` (2,066,796 KiB total directory accounting).
Published immutable archives, original workers, research files and rollback
artifacts were retained. The initially rejected force-delete command was not
executed; removal used the permitted non-force recursive command on these exact
three verified paths.

## Rapid Focus Change: Further Reproduced Stall

After 486, a deterministic read-only scheduling probe exposed another remaining
application-side delay. Two background slots were busy; a previously promoted
foreground RPC was also in flight. Selecting a different ready Agent promoted
its request into the same single-concurrency foreground queue, behind that now
irrelevant read. The new target executed only after 1.160 s when the old read was
released. No network, provider or UI rendering was involved in this stall.

The candidate fix removes the two serial promotion queues and uses the existing
direct foreground request dispatch path. Background pools remain bounded;
promotion still cancels only a not-started queued operation and the existing
once-claim prevents duplicate RPCs. In-flight reads are neither replayed nor
cancelled. The same probe executes the latest read in 0.000035 s while the old
read remains blocked. Both metadata and content lanes now have this regression;
the three focused tests pass, including original identity/merge and promotion
deduplication checks. This removes a client-imposed wait, not the latency of a
slow request for the currently selected target itself.

This is a 487 candidate until the subsequent full-test and release/install
receipts are recorded. The existing web collaboration conversation was checked
and still has the explicit `Unknown error`; no reply or agreement is claimed.

## 487 Published; User Activity Deferred Installation

Full native regression passed 741 tests in 66 suites (389.075 s). This includes
source-new switching across 40 branches (maximum 0.630 s), while a distinct
metadata/full-work barrier test completed cold history/body rendering in 0.035 s.
Loopback new/follow-up/queued submission-to-answer measurements were
0.200/0.280/2.926 s; these are not real-provider acceptance.

The unsampled release read-only production-data navigation trial completed all
42 readable branches across seven heads in 0.012-0.597 s, preserving original
history IDs, cursors and final body markers. As above, backend oracle reads
prewarm history and six genuinely missing native sources remain unavailable.
No installed user click-to-paint timing is asserted.

Release `macos-release-16dcc2a05d8240859574e37fad0a52be` was published at
00:38:21 UTC, archive SHA-256
`beb8883a10f1e15b80120215344604960576eb8e0b46ce7c980881feed358ee0`,
binary SHA-256
`dc764dd9346c53ba8fdb4119535a0cb4388311fb35e93c8d0bc6c383a3073485`.
Strict signing, packaged rendering, authenticated catalog/ticket/download and
native staging verification passed.

The final pre-exit UI check showed the user actively switching from cross-day
movement to fine-hand decoding. The prepared helper was therefore cancelled
without exiting the original app. Its receipt explicitly reports
`ready=false`, `installed=false`, `rolledBack=false`; helper PID 14941 exited
and client 486 PID 11990 remained. The user was asked for a normal-client-restart
boundary and actual-window timing permission. **487 is published/staged, not
yet installed at this checkpoint.** Existing agents, questions and messages were
not touched. Account allowance was still zero at the last check.

An additional inactive `source486` build tree (686,880 KiB directory accounting)
was removed after checking for open handles. The six owned remote staging ZIPs
for 482-487 (90,766,456 bytes) were removed only after verifying each exact
published immutable archive and authenticated-download hash. Published copies
and rollback releases remain intact. Public normal-trust TLS `/healthz` returned
200 in 0.059 s; Nginx master 307394 and Relay master 1264922 remained unchanged.

## Cold Workspace Discovery: Remaining Local Delay

While the client restart awaits the user's safe boundary, an isolated disk-cache
probe reproduced another application-imposed delay. Discovering one workspace
decoded the complete nested conversation bodies of all 64 cached runs before
filtering their workspace identities. With 10,000 original fixture history
entries per record, inside the existing cache budget, discovery took 1.173 s
before any rendering. The regression failed both the one-second limit and its
assertion that only the requested body should be decoded.

The 488 candidate decodes the existing small identity fields first and then the
matching full record. It uses the standard structured decoder, not substring
parsing, a new sidecar index, a new cache format, or truncated histories. The same
probe took 0.188 s and decoded one body rather than 64. Cross-machine filtering,
another workspace, external replacement, draft/queue continuity and cache
eviction/merging checks passed: 14 focused tests. This is a component benchmark,
not an installed click-to-paint or provider-latency acceptance claim.

A separate three-second sample of the untouched installed 486 client at
08:47:46 local time showed all 258 main-thread samples in the normal event wait,
not a UI-update loop. PID 11990 remains the running client. No restart, account
change, real-provider request or original-task recovery was performed here.

## 488 Release, Installation and Account Verification

The preceding paragraphs are time-specific checkpoints, not the latest installed
state. After the user approved the normal restart, **0.1.488 was officially
published and installed**, including 487's obsolete foreground-read fix and the
workspace-body decode filtering described above. Release ID:
`macos-release-b7aeb35a39734a24af082590ae35ca1b`.

- Archive SHA-256: `6db1105657a5384007a68c9d9d48733b26245a19ef564427dc21708a7561674d`.
- Installed binary SHA-256: `7e086dc5db412248f103eef7ccf2ff0e4616e0a4ef12dadeecdc0afc944d1bc3`.
- Strict signing, authenticated catalog/download and native staging checks
  passed. Actual client PID changed from 20557 to 22972 after a normal exit;
  no force quit was used. The installer readiness file alone was not used as
  proof of installation: the installed bytes and running process were checked.
- pcrd 0.1.122 PID 3487 and all 11 original detached workers were unchanged.
  Original drafts, outbox, answer store, handoffs, queues and user-stopped Agent
  states matched the preflight snapshot. There were no live native turns at
  this update boundary; this is not a claim about preserving active remote
  computations that were not independently measured.
- Native regression: 742 tests in 66 suites passed in 329.413 s. The earlier
  failing fixture run is retained. Its history fixture schema and socket retry
  fixture were corrected without relaxing their latency or identity assertions.
- The user switched accounts. Subsequent isolated native responses succeeded;
  the old zero-allowance condition no longer explains current test latency.
  No account switch, token refresh or allowance purchase was performed by this
  maintenance work.

Evidence: `artifact-verification-488.json`, `client488-installed-verification.json`,
`native-488-regression-result.json`, `source-488-validation-repair.json` and
`safety-client488-after-queue-and-ui.json` under the private evidence root above.

## Real Provider and Original Queue Deadline

The opt-in native probe uses a separate temporary workspace, private state and
one candidate, with no synthesis or file/research tools. It exercises the real
AppModel submission, native thread resume, response projection and offscreen
AppKit conversation surface. It cannot send to an original user workspace.
The selected model and effort remain `gpt-6.1-sol` / `max`.

The latest valid three-step trial measured:

| Path | First matching native answer on the test surface |
| --- | ---: |
| New question | 9.398 s |
| Follow-up in the original native thread | 8.456 s |
| Queued follow-up, from the original countdown deadline | 9.121 s |

The queue item `e9b8af4f-e5f4-4ebe-98b4-23dbcdb52627` retained its identity and
configured two-second test countdown. The predecessor finished naturally. The
original deadline was `1793151683135` ms; dispatch began 12.27 ms later, not
before eligibility. Its original command was submitted once, and all three
turns retained native thread `01a1802b-04c9-7e13-a5fa-b1dda590e7aa`.
No production queue delay was changed. Evidence:
`isolated-full-provider-488-original-countdown/{measurements,requests,runs,queues}.json`.

**The ten-second requirement is not consistently met.** An earlier trial
measured 11.476 s for a new question and 10.282 s for a follow-up. Those failures
remain in `isolated-full-provider-488-queue-corrected/measurements.json`; the later
passing trial does not supersede them. These short, isolated contexts also do
not establish latency for the user's long research conversations. Native setup,
account verification, thread resume and provider response time are recorded
separately; account verification is not bypassed to improve the numbers.

Two queue-probe fixture errors were separately corrected: reading an item ID
from the legacy local queue instead of the actual daemon queue, and persisting
the test countdown after, rather than before, initial settings. Their failed
evidence is retained. One owned synthetic child outlived the first test; it
finished naturally, its idle owner exited through its shutdown marker, and only
that temporary fixture directory was then removed. No research Agent was stopped.

## Actual Window Checks and Remaining Acceptance Gap

The installed window was briefly navigated across the five competition
workspaces, without sending or adopting anything, and restored to cross-day
movement / synthesis 2. History and current content were present in the checked
states, including 50 history markers in the retained fine-hand AX observation.
The initial fine-hand counter was corrected against the same observation: its
regex incorrectly required an ASCII word boundary before Chinese text.

These checks **do not prove click-to-paint under one second**. Full accessibility
queries instantiate and lay out offscreen collection items; the sampled actual
navigation captured that query overhead. Screenshot return time alone exceeded
one second even with the correct current content already displayed. Neither
that tool overhead nor a fixed amount subtracted from it is an application
frame measurement. The earlier 42-branch offscreen test also prewarmed the
backend through its oracle reads, and six missing original rollout sources
remain explicitly unverified. No universal one-second claim is made.

Latest bounded backend checks remained responsive: ping 0.0008-0.0023 s,
health 0.009-0.031 s and the selected turn summary 0.018 s. This is endpoint
evidence, not a substitute for whole-conversation rendering or model responses.
An additional import-time check measured 0.143 s for the native adapter package;
it does not establish the entire roughly one-second launch phase as Python
import overhead, so no speculative package-initialization refactor was made.

Only the owned remote 488 staging ZIP was removed after matching the published
archive and authenticated download. Published releases, rollback material,
research files and failure evidence remain. **488 is installed; the universal
1 s / 10 s performance acceptance is still incomplete.**

## User-Authorized Acceptance Workspace and Schema 62 Recovery

The user requested that maintenance design its own workspace. `PCR 性能验收`
(`ws-a5957f4c1c40471c`) is registered under the private evidence directory,
not in any research workspace. A synthetic Event Ledger project uses a 43,859
character initial prompt, 439 replay records and two real candidates. The
installed client submitted it with GPT-6.1 Sol / max / Fast off, no synthesis,
and the inherited 60-second queue countdown unchanged. All test instructions
restrict work to this synthetic project; no research messages were submitted.

The first observer accidentally omitted its sleep and ran a tight loop for
71.45 seconds. It was stopped without stopping either candidate, corrected to
a bounded 100 ms incremental read, and its evidence retained. The first
new-question timing is **not a valid natural-load latency sample**. Subsequent
measurements use the corrected observer; no fixed overhead is subtracted.

The first queued follow-up exposed a deterministic pre-native failure. Its
selected candidate used CLI state migration 58; adoption promoted its original
thread into the desktop database at migration 62. PCR's reviewed schema set
ended at 61, so continuation rejected that database before submitting input.
The same original queue command remained retryable. Migration 62 was reviewed:
it adds nullable `attached_thread_parent_id` and a partial index, with no new
table or foreign key. Only that exact schema/migration fingerprint was added;
unknown mutations still fail closed and unrelated parent histories are not
copied into candidates.

pcrd 0.1.123 was built and added to the established local wheel/manifest bundle,
then normally activated at 03:36 UTC. The macOS client stayed on 0.1.488 and
Relay was not restarted. All eleven original detached worker PIDs/start times,
user-stopped Agents, drafts, answers and original queue identity were preserved.
This is **local wheel publication and activation**, not proof of remote wheel
publication. Validation: 75 focused tests plus 6 subtests; four real Codex
loopback resumes across five model-fixture requests; 1,312 full Python tests
plus 342 subtests; 233 installed-package tests; 323 installed source hashes.
Wheel SHA-256: `129cfa16e7a8022da17201f49864a0bf3e1951a624ea1f251302486fb7880c81`.

The original command `follow-up-8a2673b0-d001-4895-afd3-461242ddf639-attempt-0`
retried naturally at 03:40:01 UTC, creating exactly one continuation
`pcr-20261028T034002Z-82d35f67`. Both candidates resumed the selected original
thread `01a1807e-737c-7fa1-b725-b346361c3079`, emitted new native text and completed
14 command events each, then finished successfully. The original queue emptied.
The installed window displays prior conversation, current answer and work groups.
Evidence: `acceptance-recovery-123.json`, `acceptance-recovered-window.png` and
`acceptance-native-observation-recovered.jsonl` under the private evidence root.

This recovered queue **failed** the original ten-second acceptance: first text
was 1,240.916 seconds after the unchanged original countdown deadline. Even
measured diagnostically from recovery dispatch it took 13.222 seconds; that
diagnostic origin does not reset or replace the acceptance timer.

## Long Follow-Up Checkpoint Cost

A subsequent actual-client follow-up in the same long conversation took
21.167 seconds to first native text (other candidate 22.417 seconds), with a
corrected observer and no research traffic generated by the probe. Submission
RPC took 8.495 seconds. Native turn submission was 13.439 seconds after the
click, then first text took another 7.728 seconds. Therefore provider latency
cannot explain the whole delay. No first-UI-paint time is inferred from the
later accessibility check. Evidence: `acceptance-followup-123-measurements.json`.

During adoption, file sync itself took 0.064 seconds and session promotion
0.021 seconds, yet each timing-phase publication rewrote the roughly 23 MiB
shared registry containing archived runs. The execution path unnecessarily
waited on this full merge/encode/fsync for display-only progress. The proposed
124 change treats `run_phase` and `agent_phase` like existing replayable text
and token events: keep the original per-run event, rebuild timing on reads,
and reserve shared checkpoints for business-state changes. Original sync
proofs, finalization journals, result/owner transitions and receipts stay intact.
It does not migrate the state format or change old pinned worker code.

A sequential synthetic benchmark with a 25,993,437-byte registry reduced six
timing callbacks from 6 global writes / median 0.874 seconds to 0 writes /
median 0.0021 seconds; all six original events remained. This is the callback
cost only, not an end-to-end latency claim. A cold-reader regression verifies
both timing reconstruction and unchanged checkpoints, followed by durable
publication of an actual `agent_started` business event. Initial overlapping
benchmark samples are retained but are not the reported comparison.

The separate controlled startup-barrier experiment showed native initialize,
account verification, thread start and turn start continuing within 16-19 ms
while the parent progress callback was held for two seconds. This rules out
that callback as a mandatory small-startup native barrier, not all possible
large-output pipe backpressure. It does not justify skipping authentication.

The latest ChatGPT Web collaboration update was attempted in verified Pro 5/5
mode, but the page returned an unknown error; one retry also failed. No answer
or independent endorsement was obtained for this update.

## 124 Activation and Checkpoint Generation Follow-Up

124 passed 1,313 full Python tests plus 342 subtests and 234 installed-package
tests; all 323 installed Python source hashes matched the frozen source.
Wheel SHA-256: `aa65cd8180f1e215fb7e4bfc5000f08bda9d46a61dd4eb554912c09fa54190d9`.
It was added to the local wheel/manifest bundle and normally activated at
04:09 UTC without restarting macOS 488. Immediate retirement/activation
evidence preserved all twelve worker identities. A later snapshot confirmed
manager 55433 loading 124, unchanged client draft/outbox/answer hashes and an
empty queue. Two research owners subsequently entered separate `retry`
operations at 04:12/04:14 with new PIDs; do not describe this later snapshot as
all original PIDs unchanged. This maintenance turn did not send those retries.
The previously user-stopped synthesis Agents remain killed. No remote wheel
publication is inferred from local publication.

The earlier Web error was transient: its completed Pro 5/5 reply was later
available. Further Pro 5/5 discussion identified a concrete merge-risk
hypothesis, then a local regression reproduced it: a late display-only event
raised a stale run's `updated_at` above a peer's completed business checkpoint.
A later unrelated save could then replace the completed result with running.
All four replay-only event cases failed before the fix. The proposed 125 change
leaves event timestamps/progress projection intact but does not let those
events raise the whole-run business merge timestamp. This is a reproduced
concurrency counterexample, not proof that it caused a specific past live
incident. The Web partner's agreement is not validation evidence.

125 also checks the exact known state-file generation only after taking the
existing writer lock. A known, fully loaded/merged/published registry avoids
redundant JSON parsing; a peer replacement still takes the original complete
merge path. Regression coverage includes a peer write while waiting for the
lock, same size/mtime with a different inode, explicit deletion on both paths,
repeat fast publication after merging a peer, and publication failures. The
stable lock file, state format, business writes, fsync and atomic replacement
are unchanged; no new source of truth is introduced.

A separate 481-run / 2,405-result synthetic registry benchmark (22,508,448
bytes) records fast-path hits and peer-changed misses. With no intervening
peer write, median-of-three per-write medians changed from 0.2833 to 0.1469 s.
With a peer replacement before every write, all six reads and writes remain,
as required. This bounded gain does not remove full-registry encoding or lock
contention. The earlier 0.874 to 0.0021 s figures describe **six phase callbacks
combined**, not each individual callback. Neither benchmark establishes the
10-second end-to-end requirement.

## 125 Activation and Actual Acceptance Workspace

125 passed 1,319 Python tests plus 350 subtests, 240 installed-package tests,
323 source hash checks and actual 124-writer/125-reader interoperability.
Wheel SHA-256: `dc5fa44674690d162a6800f3c2226855de73d3f725e6063484833309baf5f4b3`.
The local bundle/manifest was updated and manager 65027 activated normally at
04:45:56 UTC. Twelve detached owners and three live original native process
identities/receipts were preserved at activation. No macOS or Relay restart,
research input replay, user-stopped Agent revival or account change was made.
This is local backend activation, not remote wheel publication.

The user authorized designing our own workspace rather than asking them to
supply a testing question. `PCR 性能验收` uses an isolated EventLedger coding
task, a 44 KB initial prompt, two candidates, no synthesis, Sol/max, Fast off,
and the unmodified 60-second queue countdown. It does not read competition
data or create remote jobs. Actual-client follow-up command
`52d3056a-ad31-47b2-a3bb-4ff3818f8ca5-attempt-0` produced one continuation
`pcr-20261028T045004Z-4cde573c` of the original selected native thread.

That follow-up still **failed**: first native text was 24.407/24.822 seconds
after the actual click. The RPC started at click +0.795 s and finished at
+10.607 s. The first native turn submitted at +19.191 s, with first text
another 5.216 s later. Both candidates subsequently completed naturally.
No AX traversal occurred during this timed interval. The parent owner was
still pinned to 123: its finalize operation spanned 7.104 s, while recorded
file sync/session promotion themselves took 0.121/0.050 s. Updating a manager
does not hot-patch retained owners, so this is not an end-to-end 125-only
benchmark. It is nevertheless a real user-path failure, not excluded from
acceptance. Evidence: `acceptance-followup-125-measurements.json`.

## Client Cache Read/Write Contention

A later two-second sample of installed client 488 found 1,265/1,355 samples
on the serial conversation-cache queue, including 730 samples encoding a
complete cache record. This is a sample distribution, not invocation counts.
The main thread was mostly waiting. A later AX timeout alone does not prove
a frozen UI, and this sample does not explain the earlier native submission
delay. The code does show cold hydration queued behind full cache writes,
with the same lock held during merge/encode/write.

The next client change separates the single writer queue from hydration,
encodes immutable Sendable value snapshots and prepares a private file outside
the read lock, then rechecks the exact baseline and removal tombstones before
atomic publication. A changed same-lifecycle baseline is remerged; deletion
rejects the old write. Accepted-handoff still awaits its actual durable cache
publication before retiring its receipt. The cache schema, full conversations,
per-Agent identity checks and newer-memory merge rules are retained. This
removes a dependency, not all full-serialization CPU cost, and does not claim
the universal one-second/ten-second acceptance limits.

The first 489 full native run failed with 13 issues in 746 tests; its original
log is retained. Five affected tests passed when isolated, which does not
erase the failed full run. Investigation found an answer-tail assertion that
waited only for the independent work cursor, not the answer cursor, and a
separate legacy shared-stream navigation path that still waited for the
background 500 ms poll interval. This latter code finding does not explain
all timing failures from the modern body-reader fixture. The assertion now
waits for the actual answer EOF while its other reader remains held. Newly
focused legacy shared-stream navigation requests
its next page without that polling delay, preserving the existing in-flight
read and background cadence. A separate regression covers this path.

Cache recency touches are also skipped while the single writer is active:
otherwise a read of the same record can invalidate the writer's baseline and
force the same large snapshot to be encoded again. A held-encoder regression
checks that ten intervening reads do not cause a second encode. External
atomic replacements still require remerge; they are not ignored as touches.

## 489 Publication and Long-Conversation Acceptance

489 passed 748 native tests in 66 suites, was formally published, downloaded
through the existing authenticated updater, verified and normally installed
at 06:06 UTC. Archive SHA-256:
`7fc678bcd624320a5a3befffa1106141ae2ffe0f9db881699f17584f0f15c6dc`.
Loaded binary SHA-256:
`e2945193f5f754e7853d428ee148e6183dc41c2cd80f951b892b71f5e7d56214`.
The twelve worker identities and persistent draft/queue/answer/handoff stores
were preserved. One original native process completed normally during the
update: its unchanged identity/thread/turn, completed receipt and new errorless
`turn.completed` event prove completion, not a client-update interruption.

The actual 489 client continued the isolated long EventLedger conversation
from a 125 parent owner. The two branches produced their first new native
text after **15.654 / 17.844 seconds**. The original sixty-second queued
follow-up then produced first text **23.153 / 16.423 seconds after its original
deadline**. Both cases fail the ten-second requirement. The queue waited for
natural completion, retained its original item ID and deadline, and dispatched
one child. No research messages, account changes or task interruptions were
used. Both test runs subsequently completed naturally.

The `submitting` phase began at click +7.549 / +8.862 seconds for the direct
follow-up and deadline +9.590 / +9.363 seconds for the queue. This is a PCR
phase before `turn/start`, not an upstream transmission timestamp. Even after
observed native `turn.started`, the slower queued branch needed 13.463 seconds
for its first text. Do not call all of this pure provider latency, or claim
that removing local overhead alone establishes universal ten-second replies.
First UI paint was checked later, not precisely timed; the failing native
times already bound the user-visible response from below. The evidence is
`acceptance489-measurements-verified-journals.json` in the private evidence root.

The Pro 5/5 review independently challenged the earlier provider attribution
and identified per-run durable state as a structural alternative to the shared
22 MB registry. This is a design direction, not implemented validation. It
cannot be safely activated while older owners still require the existing
whole-registry protocol without an explicit compatibility design. No process
pool, authentication shortcut, database migration or speculative input was used.

## 490 Presentation Hot-Path Follow-Up

A bounded sample of the real 489 client found transcript sanitization under
the UI loading-state getter. Two new regressions reproduced eager full-body
projection when the workspace was not hydrating, or a pending submission
already ruled out a blocking loading screen. The loading decision now evaluates
the body lazily only when it can affect that decision. Boolean semantics,
history completeness and message state are unchanged.

Final-answer mirror comparison also sanitized and fingerprinted both operands
even when the original strings were exactly equal. An equality fast path keeps
the existing identity/turn gates and full normalization for nonidentical input.
In a synthetic 40-pair / 12.8 MB formatter regression, this reduced the measured
pass from 0.652 seconds to 0.000074 seconds. This is a formatter benchmark, not
a claim about actual window switching. Both new readiness tests failed before
the fix; all 15 focused projection tests then passed. Full native regression,
signed publication and installation are separate gates.

## 490 Delivery and Remaining Acceptance Gap

490 passed 751 native tests in 66 suites (foreground-interaction suites were
excluded), the signed packaged rendering check and authenticated update
download. It was formally published at 06:55 UTC and normally relaunched at
06:57 UTC as PID 7367. Archive SHA-256:
`d7a4fe66c8555011b1abe74ed0511d76941da5351d7647e9a087e43dfc5c0a80`.
Loaded binary SHA-256:
`cfda8f4f37ce2160f8058b048ebd9e42acc49cc5bfd191bb77b1b782023dd8c8`.
The manager remained 125/PID 65027, with all twelve owner processes and eight
then-live native processes preserved. Draft, outbox, accepted-handoff, queue,
answer and user-stop evidence matched across the update.

One native receipt legitimately changed during this interval. The original
Agent 3 in `pcr-20261028T052416Z-7c87ff45` received `serverOverloaded`, then its
existing executor continued in the same native process and thread. The bounded
6,595-byte event delta contains the capacity error, existing recovery event,
new matching turn and subsequent native item events. The initial strict
same-turn verification therefore failed; this exception was investigated and
recorded explicitly rather than claiming that every original turn stayed
unchanged. The client update did not send a retry or replacement message.

The real 490 window restored the original compression workspace, selected
synthesis Agent and empty composer. Read-only navigation visited the isolated
acceptance workspace and its second branch, then restored the user's position.
Click plus complete AX observation took 1.031 / 0.793 / 1.201 seconds. A separate
visit to the active fine-hand workspace took 3.496 seconds with 53 history turn
controls present; a subsequent unchanged AX read itself took 2.599 seconds.
These include automation and accessibility traversal overhead, not exact first
paint timestamps, and do not establish the universal one-second requirement.
The bounded three-second sample found the main thread mostly idle; it also
retained background cache decoding/encoding and projection work. It does not
prove that the entire AX observation interval was UI rendering delay.

The own-workspace long follow-up and original queued-message measurements
above still fail ten seconds. Neither model/effort/Fast nor the sixty-second
queue countdown was changed. No research prompt or live research operation
was used as a performance test. There is no goal or scheduled follow-up.

Verified unused 489/490 frozen-source build intermediates (1,323,888 KiB by
directory accounting) and 30,298,344 bytes of remote upload staging were
removed. Original sources, signed published archives, rollback programs and
failure evidence were retained. Cleanup did not touch research workspaces or
the user's checkout build directory. Overall latency acceptance remains open.
