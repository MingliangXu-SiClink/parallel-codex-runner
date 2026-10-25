# Save Result Ownership Across Workspace Navigation

## Scope

Baseline: installed macOS 0.1.473; pcrd/runtime remains 0.1.105.
Working repository: `/Users/mingliangxu/Desktop/parallel-codex-runner`.
No production finalize, research input, answer, queue mutation, worker interruption,
daemon restart, capacity-policy change, or remote-file operation was used as a test.
Private evidence: `~/Library/Caches/ParallelCodexMaintenance/SaveOwnership-20261025/`.

## Evidence

- The real current workspace was human epidural motor decoding, selected synthesis 2,
  not an assumed match to the screenshot's synthesis 3. The live UI showed Saving
  and a local-service connection warning.
- Original run `pcr-20261023T174618Z-d8773f9f` had `finalized_agent=10`,
  `synced_back=true`, and a complete finalization journal. Bounded recent command
  receipts also confirmed finalized/synced-back results; later repeated user
  requests existed. No maintenance request reissued those operations.
- Capabilities/controller/followups returned; health and run reads timed out at
  12-15 seconds. A two-second daemon sample showed a request thread repeatedly
  copying concatenated Unicode strings, with other requests waiting on RLock.
  This does not identify the Python source line or establish that all delay was
  caused by client navigation. The slow service path remains a separate boundary.
- Before delivery, the two originally outstanding receipts were read again by
  identity, not inferred from an older save: `6d339a1c-d7c2-4aa8-ba27-ceb63882a7d8`
  completed at `2026-10-24T16:47:01Z` for `pcr-20261023T174618Z-d8773f9f`, Agent 10;
  `8cc5fda9-7c4c-42ba-a239-de300f41f92c` completed at `2026-10-24T16:51:58Z` for
  `pcr-20261023T170833Z-188945b0`, Agent 1. Both original results were finalized
  and synced back. No maintenance mutation or daemon restart caused this.

## Reproduced Defect And Correction

`acceptSelectedAgent` captured the original target for `turns.finalize`, but
discarded the result when the original run was no longer visible. This skipped
both cache settlement and polling of a still-finalizing operation. Its Boolean
success also depended on the visible page. Background metadata reads did not
independently persist the updated original summary.

New production-path loopback tests on the unchanged code failed 12 assertions:
save completion was reported false, original memory/disk state stayed unsaved,
returning to the workspace restored that state, and pending finalization was
not observed after navigation. See `red-native.log`. The first attempted build
used the machine's default 27.0 SDK and failed on missing SwiftUIMacros before
tests ran; this is recorded separately in `red-tests.log`.

The repair commits observations to the original machine/workspace/run and its
cache independently of current navigation, retaining existing transcript/history
coverage. Only the visible projection and settings require the current-page
guard. Pending finalization starts the original run's existing read-only polling;
completed background observations are persisted. Lost replies are verified by
one original-target read, not another mutation, including navigation during that
read. Older observations cannot replace newer metadata. Completion requires the
matching finalized Agent and synced-back result; processing alone is not success.
A late parent receipt cannot change a child workspace head, draft, or settings,
and cannot dispatch another visible workspace's follow-up queue.

Additional review covered returning to A before its reply (including a new
selected Agent), readback failure after a lost reply, and the Boolean caller
`updateResumeSession`. That caller now rejects a changed navigation generation;
otherwise a successful offscreen save could apply its old setting to B. An
unavailable readback keeps the existing original-run reader alive until a valid
observation arrives; read errors are not persisted as transcript content or
interpreted as permission to replay the mutation. This adds no new durable
mutation journal or backend recovery protocol.

A second interleaving was found during review: a background metadata request
could start while A was hidden, complete after returning to A, and end polling
with a finalized cache but an old visible `activeRun`. The new readback test first
failed both visible finalized/synced-back assertions. The shared observation
commit now also projects into the currently matching run, regardless of where
the read started. It preserves the current selection and draft. See
`background-return-before-fix.log`; its initial extra transcript assertion was
isolated from the fixture's concurrent answer preloader by holding that read.

The first complete-suite attempt was deliberately stopped (only its own
`swiftpm-testing-helper` PID 20968) to add this discovered regression before
release. That interrupted run is not a successful gate; final frozen-source
gates must be rerun. No production process was stopped.

An additional broad run with `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1` exposed
an existing fixture limitation in `RunAvailabilityTests`: its detached server
performs a blocking 400 ms poll after each reply and exits on idle, while the
single cooperative worker cannot schedule the caller's next request. Its exact
three-request assertion failed, followed by client reads of that closed fixture.
The sampled socket was a temporary `pcr-*/s`, not the production daemon. Only the
test helper PID 23366 was stopped. The full focused suites and canonical native
gate use their normal pool; the changed `WorkspaceReadinessTests` additionally
run under the strict pool. The failed/interrupted broad strict attempt is not
counted as a passing gate, and no production workaround was added for it.

The first final focused run completed 176 tests with two new-fixture selection
failures: assigning `selectedAgent` directly did not record the explicit user
selection used by navigation readback. The test now uses the production
`selectAgent(2, manageFollowUpQueue: false)` path and asserts that it committed
before releasing the save reply. Production selection policy was not weakened.
The strict fixture run then exposed a competing canonical-history replacement
of the test's deliberately different `earlier` entry. That history read is now
held while checking receipt preservation; the existing pagination/coherence
tests still exercise completed authoritative history refreshes separately.

## Verification And Delivery

Frozen-source gates passed:

- 703 tests in 63 suites, canonical native gate, 371.346 seconds.
- 176 tests in 6 focused suites, 159.838 seconds.
- 58 WorkspaceReadiness tests with `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1`,
  11.869 seconds.
- Contract, icon, signing-store, unsigned-build, signed-build, strict signature
  and archive gates. Native tests used macOS SDK 26.5, native build system,
  two build jobs, no parallel test execution and no foreground interactive QA.

macOS **0.1.474 / build474** was signed with the existing
`Parallel Codex Release Signing` identity, formally published to the existing
private release catalog, and read back successfully. The archive is
`release/clients/macos/Parallel-Codex-macos-0.1.474.zip`, 14,975,817 bytes,
SHA-256 `70d36cedc708c5526ac9430e7fe1be6f8aafc9b1c2393d588b3bdfe90bbc9ea6`.
Only the four production Swift files, WorkspaceReadinessTests and Info.plist
changed in the hashed release sources relative to 473. Backend remains 0.1.105.

The authenticated update check returned IpcError after 3.737 seconds. No token
refresh or authentication change was attempted. The exact formally published
archive was instead staged through the unchanged native signature verifier.
This is not evidence that the authenticated download path works.

The old client PID 35655 exited normally. Installed 474 runs as PID 31022,
with the loaded executable inode and bytes verified against the published app:
SHA-256 `eafdf67912053bb4a95967abfd3670017dd619477036bc7d77c7bddbdfbe90a0`.
No forced termination occurred. The daemon PID 4374 and its LaunchAgent,
10 original workers and 40 native descendants retained their process identities.
Persistent drafts, outbox, accepted handoffs, queues and answer storage retained
their protected hashes. Seven original recent finalization command identities
and fingerprints were preserved. No production task mutation was sent and no
user data was rolled back. See `publication.json`, `server-publication.json`,
`native-prepared.json`, `normal-quit-result.json`, `installation.json`, and the
before/after evidence in the private directory.

Actual UI verification was completed separately after installation:

- The initial old cache still displayed completed/unsaved until metadata arrived.
  A bounded read of the original run returned finalized/syncedBack after
  **29.555 seconds**, not a new save operation. Health/list reads also remained
  slow. This backend latency was not repaired by the client ownership change.
- The original run then displayed **已保存**, **综合2已采用**, and
  **续问使用 综合2 · 已保存**, with no Save Result button. Disk cache agreed.
- Read-only navigation to the memory workspace and back immediately restored
  the saved state. No save/adoption/send/answer control was clicked.
- The user's memory workspace, Agent6 selection and blank composer were restored.
  The app remained responsive. See `ui-acceptance.json` and
  `ui-original-saved.png`; `installation.json` records the earlier installation
  stage, before this separate UI gate completed.

The final input recheck deliberately did not accept a changed whole-answer-store
hash as preservation proof. It found no removed/added answer IDs or draft-count
change, and no change in protected desktop input fields. One pre-existing answer,
`7296AD04-A7A2-4602-B3FD-BE324C467BE7`, had advanced from pending to accepted
during ordinary client readback. That stored status alone is not evidence of
native Agent execution. No answer was entered or resubmitted by maintenance.
See `post-ui-input-comparison.json` and `final-preservation.json`; the latter also
rechecks installed bytes and source hashes against the formal publication.

The deterministic tests exercise saving while navigation is in flight; the
production acceptance checks only original receipts and subsequent read-only
navigation. This does not prove every workspace or remote computation healthy,
nor resolve the service latency or update-check failure. Follow-up diagnosis
should trace `_turns_get` through `get_turn` and the manager's locked read path
without replaying production operations or restarting healthy workers.

Existing source and untracked migration work were preserved. `PCR_HANDOFF.md`
is the concise project handoff; `MAIN_AGENT_HANDOFF.md` remains historical and
contains superseded requirements. Technical review stayed in the existing
ChatGPT Web conversation using verified maximum Pro mode. Its suggestions
informed the extra race tests, not the acceptance verdict (`web-review.png`).
