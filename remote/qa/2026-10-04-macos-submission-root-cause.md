# Retained Continuation: Actual Root Cause and Recovery Acceptance

## Actual Evidence

Read-only observation on 2026-10-04 at 22:42 CST found the formal macOS client at **0.1.409**, and the healthy, paired scheduler at **0.1.69**. Staged scheduler **0.1.70 is not enabled**. There is one active detached run in a different workspace and conversation; no scheduler restart was performed. The user explicitly chose to wait for all tasks to finish.

This investigation matched the screenshot's retained submission by its original command ID, authenticated client scope and full payload fingerprint, not merely by matching visible text:

- Submission: `0e535958-958f-427d-9a0f-04acd75e4ab7`.
- Original command: `0e535958-958f-427d-9a0f-04acd75e4ab7-attempt-0-confirmed`; client scope: `mac-app`.
- Original source: `pcr-20261004T105322Z-702a431f`, Agent 7.
- Original payload fingerprint: `c3ca8270f1654d0e4715b08bf6a6869176a1e787f6f2d4803564b8860d3d4f21`. The current persisted request reconstructs exactly this fingerprint.
- At **19:22:54 CST**, the old journal recorded `failed`, no accepted result, and the exact preflight guard: `Resolve the previous finalization before continuing`.
- At **19:24:10 CST**, the source run's stored update was already `finalized`, `synced_back=true`, with no active finalization worker.
- At **22:42 CST**, the old command still remained failed and the client outbox remained `receipt_unresolved / receipt_lookup_unsupported` with its original ID, date, source and settings intact.

Sanitized evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SubmissionRootCause-20261004/actual-submission.json` and `diagnosed-command.json`. No prompt text, credentials, entire desktop state, private Codex configuration or research files were copied into the report.

## Cause

There are three distinct layers:

1. The original request reached the finalization preflight before the preceding result had finished saving. The 0.1.69 code recognizes the `finalizing` status only, rather than an active finalization worker also exposed under `stopping`. It can classify a waitable transition as a generic failure. The present records prove the exact preflight rejection and later completion; they do not independently reconstruct every historical status transition.
2. The old journal permanently caches that generic failure. Completion of the previous finalization does not automatically change the failed command record or admit the continuation. Repeating the same command against 0.1.69 returns the recorded failure rather than reevaluating the now-cleared precondition.
3. The installed 0.1.409 client keeps reconciling the original retained command, but the actual 0.1.69 service has no `commands.get` receipt protocol. The safe legacy-recovery repair exists in the separately installed 0.1.70 runtime, not in the running scheduler. Cosmetic changes to the retained-message banner do not repair this deployment gap.

The records do not support blaming a network disconnect. Preserving the message is not equivalent to accepting it or starting the next run.

## Repair and Counterexamples

The existing, sealed 0.1.70 backend repair recognizes an active finalization operation as retryable, exposes read-only command receipts, and gives only the exact proven legacy preflight record a same-identity recovery path. That qualification is consumed before an attempt can create effects. A committed accepted Run wins over a missing or failed receipt; generic failures and ambiguous history are not treated as permission to resend.

## Additional Client Defect Reproduced

A separate upgrade-related defect could recreate the same retained-message dead end for a **new** submission even after 0.1.70 was enabled. `verifiedSystemCapabilities` cached the old server capabilities indefinitely. First submission persisted the cached receipt-store ID; an old 0.1.69 cache had no such ID. If the subsequent transmission never reached the new daemon, its authoritative `not_found` receipt could not authorize safe replay because the client had failed to capture storage continuity before sending.

A counterexample seeded the old capability cache, connected to an upgraded receipt service, and simulated transport loss before any task was created. Before the change, both in-memory and durable receipt-store IDs remained nil and the original submission did not progress. The corrected counterexample failed with those specific three assertions; the initial fixture first needed adjustment for the client's existing two transport attempts.

macOS **0.1.411** now reads verified current capabilities before a first, not-yet-uncertain submission, persists the current receipt-store ID before any mutation, and checks cancellation/original command ownership after that asynchronous read. Existing uncertain submissions do not acquire a fabricated new identity or date. The counterexample then passed in 2.032 seconds, with the original command reused, exactly one logical task created, and its outbox safely handed off. This is an execution/recovery change, not a banner text change. It does not claim to be the original historical preflight cause.

This round added the missing upgrade-shaped regression rather than another UI wording change:

- One client session starts with the same retained legacy shape: original confirmed command, old first-attempt date, no receipt-store identity and an unsupported receipt service.
- The isolated service gains receipt support while that same client and delivery task remain running. Natural 60-second reconciliation handles the change; no client restart, manual retry or conversation navigation is used.
- A proven old preflight rejection executes exactly one continuation with the original identity and identical request body.
- An already accepted command opens its original result with zero additional submissions.
- The protocol-level test loads the old-format failed file into the new server, confirms that receipt lookup neither mutates it nor starts a task, then admits the original continuation once and returns the same result for a repeat.

Before the additional client cache fix, **311 tests in 39 client suites passed**, including both live-session upgrade cases in 120.082 seconds. After the change, **312 tests in 39 client suites passed** in 187.362 seconds; the old-cache counterexample failed before the change and passed afterward. **15 installed-runtime recovery tests passed**, including finalization under `stopping`, detached-worker shutdown recovery, original identity protection and protocol-level legacy recovery. **Eight read-only closure-verifier tests passed**, including rejection of worker allocation without actual Agent execution and terminal runs with only failed Agents. All verification used `nice -n 15`, one Swift build worker, guarded headless tests and isolated temporary fixtures. Final 0.1.411 release evidence is recorded below after completion.

The first client invocation used the default 27.0 SDK, which lacks the matching SwiftUI macro plugin in this Command Line Tools installation. Repeating with the already installed 26.5 SDK passed. The first selected Python invocation omitted the test-fixture import path and failed collection; repeating against the installed runtime with only `tests` added to `PYTHONPATH` passed. Neither failure changed the formal app or scheduler.

## Deployment and Closure

Production changes from the sealed 0.1.70 repair were not altered by these additional tests. Its staged wheel and installed runtime remain unchanged. The activation script still requires all candidate, synthesis and finalization work to finish and all `active_runs` / `active_operation` to be empty. It returned 75 while the unrelated task was active, leaving 0.1.69 running.

The follow-up must not close merely because the new receipt endpoint becomes available. Deployment waits for real active runs and finalization, not for this proven-unaccepted outbox, avoiding a circular wait. After safe activation, use the read-only helper:

```sh
nice -n 15 '/Users/mingliangxu/Library/Application Support/parallel-codex-runner/runtime/0.1.70/bin/python' '/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SubmissionRootCause-20261004/verify_original.py' --wait
```

It does not send, adopt, stop, retry or modify user tasks. The formal client owns recovery of its original command. The helper requires the corresponding committed Run to retain the original command identity, prompt hash and source, the durable client outbox to have handed it off, and the original target Run's Agents to have entered execution or produced a successful result. An allocated worker or a terminal Run with only failed Agents is insufficient. Only then may the follow-up report this message as recovered and delete itself. The target conversation need not be forcibly opened: a durable handoff in a different currently visible conversation is permitted. A service-only success cannot be called a resolved user submission.

**Current acceptance: repair verified in isolated fixtures; the actual retained message is not yet recovered because the running scheduler remains 0.1.69.** No QA window, app activation, physical input, Android device or formal-client restart was used.

## 0.1.411 Background Publication and Preparation

At **22:58 CST**, macOS **0.1.411 / build 411** was published and downloaded through the controller's update service, then prepared using the app's windowless built-in update verifier. The relay catalog, download and prepared candidate all matched the signed archive:

- Release: `macos-release-9be8a023a42248b49a2604b994df4196`, published at 22:56:42 CST.
- Archive SHA-256: `dc88125e224644c085a7e824ee229f2b75869a5073632bb223fccd7bd334dde0`.
- Signed executable SHA-256: `77eef4cd209d3e12d49976bd8a33cf8c66acd5fc67908589311c3ce0b55b8615`.
- The 312-test headless regression, native icon check, production build, strict signature check and packaged math/syntax resource check passed. Five previously sealed offscreen native reading tests cover the unchanged reading-view implementation; this is not a claim of new foreground acceptance.
- The prepared 411 candidate replaces the previously prepared 410 candidate and includes its transcript lifecycle fixes. The installed formal client is still **0.1.409**, with the same executable hash and baseline process identities; no installer or restart was invoked.
- All baseline Node process identities remained present and unchanged during publication/download/preparation. Android, relay and pcrd release artifacts remained byte-for-byte unchanged.

A subsequent guarded scheduler activation returned **75** because `pcr-20261004T140156Z-3d27870f` was still active. The actual scheduler remains healthy and paired **0.1.69**, with no `commands.get` or prompt-receipt capability. The read-only original-message verifier also returned 75 (`receipt_service_not_enabled`), submitted zero user tasks and did not restart the client. The original command still has the same failed preflight record and full payload fingerprint; the original source is finalized. These actual observations do not satisfy message-recovery acceptance.

Release evidence is under `SubmissionRootCause-20261004`: `staged-release-411.json`, `relay-catalog-411.json`, `update-download-411.json`, `client-ready-411.json`, `actual-submission.json` and `original-message-recovery.json`. The staged 0.1.70 wheel remains `f8daa0ec99d2d38b8ed436283dfc4db8c14a40f659201b974c058c48c588c359` and has not been enabled.

The existing follow-up `pcrd-0-1-70` was observed as **PAUSED**. Its prompt was updated with 411, the latest tests and original-message acceptance criteria while preserving that status. Resuming quiet checks was requested separately; a paused follow-up must not be described as currently monitoring or automatically applying the pending upgrade. The all-tasks-idle requirement is unchanged.

## Subsequent Client Admission Release

At 23:50 CST, the user's separate authorization for the next client release was
completed: **0.1.412 is published, installed and restarted inactive**, after 328
non-foreground client tests and signed updater validation. The client had reached
0.1.411 before this installation. Its original receipt-recovery repair is retained,
along with the new separation of command admission from passive content reads.
See [the actual 412 acceptance record](2026-10-04-macos-composer-admission.md).

This does **not** close the original retained-message incident. Actual pcrd is
still **0.1.69**, PID 9818, healthy and paired, with the unrelated active Run above.
The original outbox still reports `receipt_unresolved` / `receipt_lookup_unsupported`.
Neither the original command nor its source was rewritten or resent by this
verification. 0.1.70 remains staged and the follow-up remains PAUSED. The earlier
preparation-only observations are historical, not the current client version.

## Original Message Recovery Verified

```json
{
  "observed_at": "2026-10-04T16:28:16.774334+00:00",
  "actual_version": "0.1.71",
  "read_only": true,
  "client_restarted": false,
  "user_tasks_submitted_by_verifier": 0,
  "run_id": "pcr-20261004T162708Z-9104f9b6",
  "run_status": "running",
  "original_command_identity_verified": true,
  "original_prompt_verified": true,
  "original_source_verified": true,
  "outbox_present": false,
  "target_agent_statuses": [
    "copying",
    "running",
    "copying",
    "queued",
    "queued"
  ],
  "changed_baseline_process_pids": [
    "1865",
    "1866",
    "1868",
    "2043",
    "2044",
    "2045",
    "2046",
    "2048",
    "10169",
    "10170",
    "58802",
    "58804",
    "58805",
    "60238",
    "60239",
    "60240",
    "74270",
    "74271",
    "74273",
    "82457",
    "82460",
    "82461",
    "92213"
  ],
  "recovered": true,
  "original_run_working_or_completed": true
}
```
