# History provenance and navigation performance

## Scope and safety

Ordinary interactive maintenance only. No goal, scheduler or heartbeat. Do not
interrupt existing owners, native turns or remote jobs. Preserve user source
changes, accepted inputs, account/model/effort settings and queue countdowns.

Evidence is kept in the private `InteractivePerformance-20261027-39zHuL` directory
under `~/Library/Caches/ParallelCodexMaintenance/`. Publication, installation and
actual-window acceptance are separate from the source/test results below.

## Confirmed mechanisms

- The six repeated English bubbles are six distinct executor recovery inputs,
  not six human submissions and not a repeated projection of one native event.
  The original execution journal records upstream 429/retry exhaustion and
  bounded backoff before the matching recovery turns. Native history retained
  the application-context marker on only the first identical context value.
- macOS cache reads touched the cache file's mtime. The next read consequently
  invalidated its decoded-byte cache even though no content had changed.
- Android serialized/encrypted whole transcripts while holding its read mutex.
  Navigation could wait for an unrelated cache writer. Running-Agent backlog
  paging also stopped after one page and waited for another polling interval.
- Android polling and selection could start competing reads of the same Agent,
  replacing the request ID and discarding the other's response.

## Changes

1. Add `input_origin=pcr_recovery` only with native structured metadata or a
   matching original thread/receipt/execution journal. Unknown evidence and
   identical human text remain visible. Each future recovery context contains
   its existing client message ID, preventing native context deduplication.
2. Preserve raw history text, IDs, counts and cursors. Both clients exclude
   proven control inputs only from the visible conversation. Provenance survives
   legacy cache refreshes. Old macOS caches remain visible while a background
   authoritative refresh upgrades their history projection.
3. Track macOS read recency in memory, not by rewriting content timestamps.
   External inode/size/mtime replacements still invalidate decoded bytes.
   Plain formatting avoids unnecessary metadata/emphasis passes.
4. Serialize Android cache writes outside the read mutex and publish atomically.
   Keep removal and cancellation ordering, authenticated encryption and complete
   conversations. Coalesce identical Agent reads in the ViewModel scope, while
   unrelated identities proceed independently. Drain running-Agent pages up to
   the existing page/progress bounds instead of waiting one poll per page.

## Evidence and remaining acceptance

- Read-only projection of the original memory-task history identified all six
  recovery turns; the source rollout mtime was unchanged. No task was restarted.
- Initial backend focused tests: 107 passed, with 24 subtests in the recovery
  group. Full frozen-source regression is a separate release gate.
- macOS focused provenance/cache tests: 3 passed. Repeated unchanged cache reads
  previously caused 11 additional decodes in 12 reads; after the fix they cause
  zero, with one decode after a real external replacement.
- A 250,888-byte plain-formatting microbenchmark improved from 0.372 to 0.341 s
  for 20 passes. This is not an end-to-end navigation measurement.
- Android unit tests pass after paging, write-lock and shared-read changes.
  A real phone installation and rendering test are not yet verified.
- Prior real long-follow-up/queue measurements exceeded ten seconds. Neither
  these source fixes nor a fast local receipt establish the user's 1 s/10 s SLA.
- The initial Pro web review completed. A follow-up review could not be sent
  because the UI reported its Pro limit; no lower mode was substituted.

## Verified first delivery

- pcrd 126 activated with the original 12 detached owners and 15 native channels
  preserved. The old owners deliberately retain their original runtime.
- macOS 491 was signed, published through the authenticated release channel,
  downloaded and verified with the native updater, then normally restarted.
  Loaded executable SHA256:
  `e88a4075d38683ba5301de4ec856e6ee4b178f6f8cb633cbc00018f1c00a1f90`.
  Original messages, drafts, answers and workers remained intact.
- Android 271/build 272 was signed and published; phone installation is not
  verified. Relay code and service are unchanged.
- Frozen regression: backend 1,323 tests plus 350 subtests; macOS 755 tests in
  66 suites; Android 279 tests. Foreground interaction is a separate gate.
- Native hidden-window rendering of 34 branches across five workspaces measured
  0.015 to 0.956 seconds. Client caches were cold; server history was prewarmed.
  This is not proof of arbitrary real-window switching within one second.
- Real long-context submissions on 491/126 still failed the response limit:
  direct follow-up 19.762/20.599 seconds; queued follow-up 16.156/16.372 seconds.
  The queued clock began at the unchanged 60-second deadline. Original message
  and native turn IDs were verified; no build or AX traversal ran during startup.
- Local health and the existing authenticated Relay device-list request passed
  independently (0.022 and 0.087 seconds). This does not verify research nodes.

## Second optimization batch (delivered)

- Native startup repeatedly parses a 3,183,839-byte Codex configuration merely
  to decide whether file-backed account following is eligible. Each parse took
  about 0.18 seconds, including repeated checks during startup and live execution.
  Cache only the boolean policy decision by device/inode/mtime/ctime/size, with
  descriptor and pathname generation checks. Credentials, account changes,
  environment overrides and managed-config presence remain freshly verified.
  Usage restoration reuses the same policy rather than a duplicate parser.
- Initialization-only probes in owned temporary homes, without thread or turn
  submission, reduced pre-initialize overhead from 0.707-0.750 seconds to 0.206
  seconds cold and 0.004-0.005 warm. This is not end-to-end response acceptance.
- A detached result-operation worker no longer performs global maintenance or
  reconciles unrelated runs while its own save receipt is pending. It still
  loads all checkpoints for lossless peer merging and reconciles its own run.
  Initial loading now retains the exact descriptor generation, avoiding a
  redundant parse on the first read/write. A synthetic 506-run, 24.9 MB receipt
  lookup improved from median 0.124 to 0.088 seconds before scoped reconciliation.
- macOS confirmation of a browsed Agent incorrectly sent `reset_countdown=true`.
  It now preserves the original queue deadline. A deterministic regression
  failed before the fix and passed afterward with the same queue identity.
- Android publishes a confirmed selected destination before draining all pages.
  Foreground selection promotes an existing four-page reader to its 20-page
  budget without restarting the cursor. Canceled older readers cannot borrow a
  replacement's budget, release its slot or overwrite its readiness state.

- pcrd 127 is active; the original 12 detached owners were not restarted. Its
  wheel and plugin/local manifest are updated. This is not a claim that a remote
  backend update catalog was published. Backend regression: 1,331 tests plus
  350 subtests; installed-wheel tests: 285 and 324 source hashes checked.
- macOS 492 was signed, formally published, authenticated-downloaded and normally
  installed. All eight remaining native channels retained their identity; one
  additional channel completed naturally before verification. macOS regression:
  756 tests in 66 suites. Android 272/build 273 was signed and published.
- Real 492/127 long-context measurements still fail the ten-second limit:
  direct follow-up 11.621/23.657 seconds, queued 24.962/32.346 seconds after the
  unchanged countdown. Android compilation overlapped the latter measurement.
  These are native-event times, not client paint times. Original receipts match.
- Read-only native startup probes (initialize and account/read, no thread/turn
  submission) took 1.869-3.450 seconds including teardown. Account verification
  remains necessary; no credentials or account policy were bypassed for speed.

## Cache discovery and Android save fence

- Android 273/build 274 adds an AES-GCM encrypted, rebuildable cache identity
  catalog. Navigation no longer decrypts every unrelated cached transcript.
  Exact file generations, machine identity and filenames are verified; legacy,
  damaged and interrupted publications rebuild from records without discarding
  history. A synthetic 40-record lookup decoded one body in 7.408 ms versus
  41 decodes in 120.724 ms without the index. This is not a phone measurement.
- Saving now updates the captured original run from its matching receipt even
  after navigation. It releases the mutation fence before history refresh;
  pending finalization is not presented as saved. Late pre-receipt reads cannot
  roll it back. Android 273 passed 288 tests, release lint and signed packaging;
  it is formally published but has not been installed on a physical phone.
- macOS 493 adds the equivalent rebuildable identity catalog with owner-only
  permissions. Exact inode/size/nanosecond-mtime validation handles external
  replacements; stable reads do not rewrite the index. A 64-record synthetic
  cold lookup parsed one body in 29.092 ms, compared with 241.177 ms plus all
  64 identities without an index. Complete transcripts remain intact.
- macOS 493 passed 757 tests in 66 suites and packaged rendering/signature
  checks. It was formally published, downloaded through the authenticated
  updater and normally installed. Loaded executable SHA256:
  `aef1c9a06a73e324b4c6feac1c8faae8fea860a6755dd26def9a508f40ed3f5b`.
  Manager PID 34431, all 12 owners and eight live native channels were preserved;
  seven channels produced new native events across the installation baseline.
  Drafts, outbox, answers, queues and explicit user stops were unchanged.
- During the 493 build, 48 bounded concurrent read requests had no errors:
  run summaries median 12.82 ms/max 20.41 ms, event tails median 2.04 ms/max
  3.94 ms, health max 17.77 ms. These checks cover local reads, not remote
  computation or universal real-window navigation/response acceptance.

## Additional Android work and exact-generation reads

- Android 274 joins the existing ViewModel-owned history reader instead of
  invalidating its request ID on navigation. Markdown prewarming now works
  within the existing 256-entry/4M-character cache budget; it does not repeatedly
  parse an older prefix that will immediately be evicted. Cancellation stops
  prewarming, while full on-demand conversation rendering remains unchanged.
- Android 275 moves pure page merging/formatting off the main dispatcher and
  rechecks the original request identity after suspension. Exact-equal final
  answers bypass whitespace normalization; metadata/citation parsing is skipped
  only when its markers are absent. A 201,785-character, 20-pass equality
  microbenchmark improved from 152.675 ms to 0.160 ms. This is not phone latency.
  Release regression: 293 tests with zero failures, errors or skips; R8/lint and
  signing checks passed. Android 275/build 276 is formally published, with APK
  SHA256 `2f0f557d532476c1770a60aac3c5eef5bdb1ab470cd232a59fc387367a6570d0`.
  No phone installation or physical-device interaction is claimed.
- Backend 128 fixes a reproduced stale-cache edge case: the old path/size/mtime
  fingerprint could accept replaced bytes with the same size/restored mtime.
  Device, inode and nanosecond change time now participate in the fingerprint.
  Regression exercises both atomic replacement and in-place replacement, not
  just a changed file size. This is not proof it caused every earlier incident.
- Backend 128 passed 1,332 tests plus 350 subtests; the installed wheel passed
  286 tests and 324 source hash checks. Actual state-v3 interchange between
  127 and 128 passed. The normal manager-only switch preserved all 12 original
  owners and eight native channels; seven channels emitted new native events.
  User-stopped Agents 10/11 stayed stopped and the old pending stop receipt was
  not replayed. Relay was not restarted. The authenticated device-list request
  and local health passed independently in 0.085 and 0.022 seconds.
- macOS 493 native hidden-window rendering covered 34 branches with a maximum
  of 0.754 seconds, using a cold client render cache and prewarmed server history.
  Five automatic selection mutations were denied by the read-only proxy;
  they were not executed against production and are not transport failures.
  Actual memory-workspace inspection showed history/tools and no recovery
  bubbles; the previous workspace/Agent was restored. Neither check establishes
  one-second first paint for every real user navigation.
- Cleanup removed only verified own build directories and obsolete installer
  staging, retaining signed archives, rollback programs and all user histories.
  Five Android remote upload-staging APKs were removed only after their hashes
  matched both the local release and retained published APK. No research files
  or original workspaces were cleaned.

## Final preparation and remaining verification

- Android 276/build 277 starts the selected cached Agent's incremental read
  immediately on workspace selection. It no longer waits for the global
  workspace-directory response or a sleeping metadata poll. Existing shared
  readers, cursor promotion and post-suspension identity checks are reused.
  The uncached destination still requires authoritative metadata. Regression:
  294 tests passed; signed APK SHA256
  `aa1ef2ec8d91ce99efd09c9a43b26fc046da751d1e9ecf65ddbfcdfa43ab3cf9`.
  The authenticated publication completed; phone installation remains unverified.
- Backend 129 removes an unconditional whole-checkpoint rewrite after reading
  an already committed worker operation receipt. Real new terminal-owner
  transitions still persist through the existing reconciliation path. The two
  regressions distinguish zero unchanged writes from exactly one real change.
  Frozen tests: 1,334 plus 350 subtests; installed-wheel tests: 288 with 324
  source hashes checked. Actual 128/129 state-v3 interchange passed. After the
  finite 128 soak finished, the normal manager-only switch activated 129
  (PID 75924), preserving all 12 existing owners and all seven then-live native
  channels. Six channels had new original native events after the baseline.
  One earlier native channel completed before the switch. Drafts, queues,
  accepted-message identities and the old stop receipt were preserved. Neither
  the client nor Relay was restarted for this switch. Local health and the
  existing authenticated device-list path passed in 0.023 and 0.083 seconds.
- An isolated eight-candidate fake-native continuation with 506 synthetic
  archived runs (about 25 MB checkpoint) dispatched in 1.109-1.256 seconds
  and completed in 2.284-2.327 seconds. No provider call, production message,
  research workspace or real credential was used. This exposes remaining
  checkpoint/handoff cost, not the ten-second real-response acceptance result.
- The finite one-hour 128 read soak completed 2,892 bounded requests with zero
  request errors. Maximums: original-run summaries 0.373 s, incremental tails
  0.116 s, local health 0.327 s and authenticated device-list requests 0.139 s.
  All 240 Relay-status samples reported the same connected generation. This
  covers sampled reads, not every client interaction or remote research job.

The one-second/ten-second universal acceptance requirement remains unmet.
Native real-response measurements above still exceed ten seconds. The latest
isolated read-only admission estimate was 0.418 seconds cold and 0.022-0.032
seconds warm; actual test-run workspace/history preparation was under 30 ms per
Agent. These do not account for the entire observed pre-submit delay, and the
native submitting-to-first-text interval also remains variable. No queue rules,
model/effort settings or authentication checks were weakened to pass a benchmark.

## Latest actual acceptance (493 / 129)

Two real candidates used the owned Event Ledger workspace, its existing native
conversation, GPT-6.1 Sol/max, Fast off and the unchanged 60-second queue delay.
The queued clock starts at that deadline, not at enqueue time. Original command,
message, thread and turn identities were checked. No initial task was replayed.

| Path | Agent | To native submission | First character | First completed message |
| --- | --- | --- | --- | --- |
| Direct continuation | 1 | 6.496 s | 13.844 s | 14.911 s |
| Direct continuation | 2 | 6.496 s | 27.936 s | 29.728 s |
| Eligible queued continuation | 1 | 6.480 s | 58.697 s | 59.897 s |
| Eligible queued continuation | 2 | 6.480 s | 65.164 s | 65.164 s |

All four measurements fail ten seconds. The first-character metric is retained
separately and is not substituted for a meaningful answer. Native submitting to
first text accounts for 7.348-58.684 s here; it includes native preparation,
network and model generation. It does not justify attributing every delay to
the provider: the local pre-submit path still takes about 6.5 s. The direct
command itself took 3.133 s; predecessor finalization took 1.818 s, including
its durable handoff. The shared state remains roughly 25 MB, about 23.5 MB of
which is retained result payloads. A state-format migration was not attempted
under legacy live owners, and no durability barrier was removed for speed.

All four original test turns subsequently completed naturally. The original
queue item was consumed once and the queue became idle. An additional save of
the final owned result completed in about 2.245 s; returning showed it saved,
without a second save. Navigation happened after the worker receipt, so this
is not evidence for the concurrent-navigation save race. The user's original
workspace/Agent and empty composer were restored; no research message, stop or
adoption was sent. Evidence: `acceptance493-measurements.json`,
`acceptance493-completion.json` and `client493-final-interaction.json`.

Actual memory-workspace checks still exposed 53 history anchors and no recovery
bubbles. AX completion took 1.017 s for a workspace selection and 3.561 s for an
Agent selection; a settled same-view AX traversal itself took 2.139 s. A bounded
client sample confirms AX hierarchy enumeration instantiates collection items.
These are not valid first-paint measurements, nor proof of the one-second SLA.
The existing native-render harness remains the narrower evidence above.

Android's installed SDK reported no attached devices. No emulator was started
under the current host memory load and no phone installation is claimed. The
temporary empty ADB server started by that inventory was stopped afterward.

## Delivery consistency and sustained reads

The 129 thirty-minute bounded read soak completed 1,446 requests without an
error. Maximums were 0.311 s for summaries, 0.157 s for event tails, 0.300 s
for local health and 0.220 s for the existing authenticated device-list path.
These are RPC timings, not model-response or client-render timings.

Final package checks matched all five manifest artifact hashes and sizes,
343 backend source files, 550 macOS source/build-support files and 136 Android
source/build-support files against their respective frozen builds. The loaded
493 binary still matches its signed archive and passes strict verification;
the LaunchAgent interpreter resolves to 129. Relay remains 21. Android 276
is published but not installed on a phone here. The installed Codex plugin
was not silently replaced. Evidence: `delivery-final-verification.json`.

At 05:51 Asia/Shanghai, a fresh supported Codex usage read returned the selected
account's weekly usage at 100 percent (`fetched_at=1793224301.23982`, refresh
idle), resetting at 2026-11-04 14:35:43 Asia/Shanghai. This independently confirms
the client's zero-remaining display; it is not inferred from a stale badge.
No additional real model test was started, no account was switched and no Agent
was interrupted. The earlier ten-second failures remain failures, not invalidated
or excused by this later quota observation.

The remaining local continuation work is concrete: the installed 129 isolated
506-archive profile still performs seven state commits (0.452 s cumulative)
and waits for the original owner's durable finalization/retirement. The real
pre-submit path is about 6.5 s, so the synthetic profile does not explain its
entire delay. Removing commit/owner barriers without replacement would weaken
delivery and risk duplicate work. A per-run persistence redesign needs explicit
legacy-owner interoperability and recovery validation before activation; it
was not slipped into the live service at the end of this session.

After confirming no process used them, six superseded build-only source copies
from this session were removed: backend 126/127, macOS 491 and Android 273-275
(106,268 KiB total allocated size). Current frozen sources, the previous backend
rollback source, signed release/rollback archives, hash manifests and test logs
remain. Installed runtimes, original Agents and user workspaces were not touched.

At 06:04, all eleven original research owner processes retained their exact
PID/start identity and input/config hash. Of the seven native channels present
after the 129 switch, five retained their process/thread/turn/message identity
and emitted new original command/reasoning events; two finished naturally with
`turn.completed`, preserving their original identities. The unchanged owner
then started its three already-configured synthesis Agents; maintenance did
not issue a start, retry or adoption for them. Original pending command receipts,
user-stopped Agent states, persisted desktop drafts/outbox/accepted handoffs and
question-answer drafts matched the baseline. The owned acceptance test was the
only owner intentionally finalized, after both test Agents completed.

Local health took 0.024 s and the separate existing authenticated device-list
request 0.100 s. Relay remained in the same connected generation. Evidence:
`safety-final129.json`, `final-native-safety129.json`, `link129-final.json`.
This proves the checked execution continuity, not a full remote-job health audit.

The second thirty-minute read matrix completed at 06:09:48: 3,696 requests,
zero errors, five original workspaces and all 34 branches exposed when it began.
Event-tail maximum was 0.131 s, run-summary maximum 0.458 s and authenticated
device-list maximum 0.234 s. This initial branch set did not include the three
synthesis branches exposed later by the normal phase transition. They receive
a separate bounded supplementary read check; the 34-branch result is not
misreported as coverage of every branch present at the end.

The supplementary eight-minute check completed at 06:18:25 with 146 requests
and zero errors for synthesis Agents 6/7/8. Across the two matrices, all 37
listed branches were covered by 3,842 read-only requests; event-tail maximum
was 0.173 s and summary maximum remained 0.458 s. The harness's default scope
description in the supplementary result was corrected and annotated, without
altering measurements. All finite test processes have completed. No recurring
monitor, Goal mode or scheduled follow-up was created.

Delivery result: macOS 493 signed/published/installed, pcrd 129 built/activated,
Android 276 signed/published but not phone-verified; Relay 21 unchanged. The
reported recovery bubbles are corrected without deleting native history or
matching arbitrary human text. Read-path improvements are measured, but the
universal one-second navigation and ten-second real-reply acceptance remains
open. In particular, actual eligible queued replies still took 59.897-65.164 s.
