# macOS 0.1.416 and pcrd 0.1.73: Queue Admission Continuity

## Incident And Proven Cause

The installed client was macOS 0.1.415 and the actual healthy, paired scheduler
was pcrd 0.1.72. Candidate Agents had completed while synthesis Agents 6-8 were
still running in `pcr-20261005T020818Z-01105da7` (D).

The queue was incorrectly waiting against an older finalized turn,
`pcr-20261004T105322Z-702a431f` (A), and inherited its manual Agent 7 selection.
D continued from B (`pcr-20261004T162708Z-9104f9b6`), which continued from A.
All three are in the same workspace and conversation. This was not a new
conversation or an Agent execution failure.

The original enqueue receipt was completed at 05:41:52.676464 UTC. Reconstructing
the request with D, the saved message UUID, prompt, settings and attempt exactly
matches its persisted SHA-256 request fingerprint. The receipt nevertheless
returned a queue bound to A. The message was created at 05:41:52.669912 UTC and
paused at 05:41:52.772097 UTC, about 102 ms later, with attempt 0, failure count 0
and no execution dispatch.

`FollowUpQueueStore.enqueue` treated a cleared queue's `last_completed_item_id`
as unconditional proof that every subsequent message was a late append to the
old batch. The old turn and Agent selection overwrote the new admission context.
The coordinator then correctly noticed `latest=D != queue=A` but incorrectly
paused this new message before reaching its normal running-worker wait branch.
Moving that wait check or changing the warning text would not fix the bad bind.

## Implementation

- An empty queue's completed marker no longer overrides a verified descendant
  turn. The controller verifies immutable continuation ancestry, conversation
  identity and workspace identity. New batches clear the old selection.
- Late old-client appends still cannot regress an already advanced queue.
  Nonempty batches and unproven lineage are not silently retargeted.
- Each new queue item records server-controlled `enqueued_run_id`. Queue edits
  preserve that provenance and cannot forge it.
- `followups.reconcile_enqueue` verifies the original completed enqueue receipt
  and its exact request fingerprint. Missing/expired receipts and another
  client's identity cannot fall through into a new enqueue. Neither normal
  receipt replay nor reconciliation replay returns an obsolete queue snapshot.
- Legacy repair requires exactly one item in both the original receipt and
  current queue, the exact automatic-pause reason, generation = receipt + 1,
  unchanged UUID/prompt/settings/attempt/creation, no dispatch and no failure.
  It only corrects the wait anchor and inherited selection in one atomic queue
  write. User edits, explicit pauses, extra items and existing dispatches are
  retained rather than overwritten.
- macOS performs this proof check in the background only for the eligible
  pause and advertised backend capability. A capabilities cache predating the
  daemon upgrade is refreshed at most once per minute in this exceptional path.
  Transient transport failures permit another check; deterministic conflicts
  do not cause new sends, new UUIDs or incremented retry attempts.
- Candidate work, synthesis work and result saving must still finish before
  the normal coordinator dispatches the queued question. Existing workspace
  admission, at-most-once dispatch and conversation-change protection remain.

## Verification

- The actual installed 0.1.72 fails the new stale-empty-queue regression.
- Source Python suite: 951 passed, 104 subtests passed (80.86 s).
- Focused queue, receipt and durable handoff suite: 220 passed,
  11 subtests passed (4.98 s), including 18 new turn-binding cases.
- Independent installed 0.1.73 runtime: 950 passed, 1 repository-only test
  deselected, 104 subtests passed (79.32 s). Dependencies stay pinned to 0.1.72.
- Client headless/offscreen suite: 339 tests in 39 suites passed (193.240 s).
  A private Unix transport fixture verifies original identity and parameters,
  backend-upgrade capability discovery and one proof check per queue generation.
- App icon checks passed. The production app build passed and packaged math
  and syntax rendering passed through the prohibited-activation smoke entry.
- The real original receipt was replayed only in a temporary private controller
  with copied run metadata. This proved recovery to D, waiting through synthesis
  and finalization, and exactly one simulated dispatch from finalized Agent 8
  using the original message UUID and attempt 0. No real task was submitted.
- The signed app retains the existing trusted designated requirement. Every
  Python source file matches the wheel and independently installed runtime.
- Tests/builds use low priority and one Swift build worker. No QA app was
  foregrounded, no desktop screenshot or input was taken, and no Android was used.
  Initial failing iterations of the new native fixture were corrected before the
  passing suite; they are not counted as successful acceptance.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/QueueContinuity-20261005`.
Important evidence includes `queue-origin-proof.json`, `saved-admission-replay.json`,
`baseline.log`, `focused.log`, `python.log`, `installed.log`, `headless.log` and
`staged-runtime.json`. Original message content was not sent to the web partner.

## Live Recovery Boundary

During this investigation the real queue changed from generation 31 to 34.
It still contained the original paused UUID. This was not a maintenance write;
the real queue hash was independently unchanged across the private replay.
The exact untouched-record recovery proof therefore does not cover that newer
queue revision. No user edit is reverted and no message is manually resent.
The verified legacy repair is not being represented as a live recovery of this
edited record. New admission prevention requires actual pcrd 0.1.73 activation,
not merely client publication or installation.

## Deployment

Before publication, macOS 0.1.415 remained installed, pcrd 0.1.72 was healthy
and paired with scheduler PID 41934, D remained globally active, and there was
no active finalization operation. Backend 0.1.73 is staged in its independent
runtime and has not been enabled. All real user tasks and queue records remain
untouched by maintenance. Publication results are recorded below after checking
the authenticated release catalog and the official built-in update endpoint.

macOS 0.1.416 (build 416) was published successfully. The authenticated catalog
and the official built-in update check from build 415 both return 0.1.416 with
the exact locally signed archive hash. Android and relay component metadata,
their artifacts, and the backend installer script remain byte-for-byte unchanged.

- Archive SHA-256:
  `693f282be2701f60aec73726a09d42053cc400d9326e6a808f7634ae2e2aa9dd`.
- Client binary SHA-256:
  `1ac7a22134a679191934ab403d7bb301bbde70e6e3582697516f45a93e2e0bd9`.
- Backend wheel SHA-256:
  `195ca264d5d2b3870890beabf33fc4d0320eed91e09c746131db20958680304a`.
- Post-publication: the installed client is still 0.1.415, PID 53876; actual
  scheduler 0.1.72 remains healthy and paired, PID 41934, with the same unchanged
  launch service definition and active D run. No install/restart approval has
  arrived for this release, so no client install/restart was performed.
- Independent pcrd 0.1.73 is staged, not enabled. It must not replace the
  scheduler while any Agent, synthesis, finalization or other operation is active.
- The real queue remains generation 34, bound to A with the original UUID at
  attempt 0 and paused=true. Its batch state is waiting after the intervening
  change. This is not described as a production recovery or silently retried.

Publication and safety evidence: `staged-release.json`, `relay-publish.json`,
`relay-catalog.json`, `update-check.json`, `after-publication.json`,
`current-queue-after-publication.json` and `publication-safety.json`.

## Independent Design Challenge

The background web consultation distinguished new admission, late append and
historical-record repair. Its missing/expired receipt counterexample changed the
implementation from repeating normal enqueue to the restricted reconciliation
action. This was verified locally with negative tests and private saved-receipt
replay, not treated as acceptance merely because another model agreed.
Conversation: https://chatgpt.com/c/6ac33a06-b5a4-83ee-ab88-5814fa0d8b25.

## Activation Request Blocked by Catalog Preflight

The user explicitly requested 0.1.73 activation. On 2026-10-05 at
14:34:44 UTC, all globally active runs and finalization operations were empty.
Build, signature, source/wheel equality, installed-runtime equality and the
existing test evidence passed the activation checks. The guarded activation
script nevertheless returned 75 before changing the LaunchAgent because
`codex.model_options` was unavailable. No scheduler restart was attempted.

The execution CLI resolves to `/opt/homebrew/bin/codex`, version 0.159.2.
The shared home cache belongs to CLI 0.160.0, so it is correctly not reused as
the execution CLI's own snapshot. An isolated refresh using the existing cache
manager also failed to produce a matching snapshot. A separately initialized,
read-only `model/list` request succeeded and included GPT-6.1 Sol among eight
models, but produced no `models_cache.json` in its private home. Therefore this
is an observed discrepancy between native model listing and the daemon's
file-based catalog reader, not evidence that GPT-6.1 Sol itself is unavailable.
The precise reason the CLI did not generate that file remains unproven.
No synthetic cache, cross-version cache relabeling or guard bypass was used.

The actual scheduler remains healthy and paired at 0.1.72, PID 41934, with
its original service-definition hash. The installed client now reports
0.1.416, rather than the earlier publication-time 0.1.415. Maintenance issued
no client install, quit or restart command. Client PID 50004 was present at
the first check but absent at the final check; this change was not caused by
an activation or client-control command, and its cause was not inferred.
All nine baseline Node PIDs remained present. The real queue remained
generation 34, paused at attempt 0 with no failures; no real queue write,
task submission, stop, adoption or retry was performed.

Evidence: `deployment-0.1.73-deferred.json`,
`activation-model-catalog-refresh.json`, `activation-model-catalog-probe.json`
and `activation-blocker-verification.json`. Backend 0.1.73 is still staged,
not enabled, and its production fixes are not claimed to be active.

## Scheduler Activation Check 2026-10-05T14:25:40.806776+00:00

```json
{
  "deferred": true,
  "reason": "model_catalog_unavailable",
  "actual_version": "0.1.72",
  "scheduler_restarted": false,
  "client_restarted": false,
  "queue_writes_by_maintenance": false,
  "detail": "pcr_error: Codex model options are temporarily unavailable. Please try again later.",
  "health": {
    "healthy": true,
    "detached_workers": true,
    "active_runs": [],
    "active_operation": null,
    "global_active_runs": [],
    "global_active_operation": null,
    "prompt_delivery": {
      "running": true,
      "pending_count": 0,
      "active_count": 0
    },
    "prompt_admission_count": 0,
    "link_state": "connected"
  },
  "installed_client_version": [
    "0.1.416",
    "416"
  ],
  "checked_at_utc": "2026-10-05T14:25:40.806776+00:00"
}
```

## Superseding Activation Completed

The model-catalog blocker above was repaired rather than bypassed. Published
0.1.73 artifacts were not overwritten. The immutable 0.1.74 backend includes all
0.1.73 queue fixes byte for byte and separates real `model/list` menu metadata
from the CLI's optional native execution cache.

At 2026-10-05 15:22:21 UTC, after repeated full-manager idle, finalization,
operation, admission, and delivery checks, the scheduler changed from 0.1.72 to
0.1.74. It was independently verified healthy, paired and connected with a real
eight-model catalog including GPT-6.1 Sol and the enqueue-reconciliation action.
The actual interpreter and subsequent production check confirm activation, not
merely staging. No client restart, QA window, Node restart, real task submission,
queue retry or queue edit was performed.

The generation-34 paused record had already been edited after its original
receipt; it does not satisfy the narrow automatic-repair version guard. Its item
identity, attempt and queue state remained unchanged. This is not reported as a
successful live resend. Details and final test/deployment evidence are in
`2026-10-05-pcrd-model-catalog.md` and
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ModelCatalog-20261005/`.
