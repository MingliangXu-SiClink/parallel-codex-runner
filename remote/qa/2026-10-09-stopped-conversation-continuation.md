# macOS stopped conversation continuation

## Scope and authorization

The user manually stopped the TunLSCI cloud-cleanup request and wants to ask later
questions in the same conversation. The cancelled cleanup must not be resumed,
resent, retried, adopted or recreated. The user authorized publication, installation
and a background-only restart of the tested macOS client. This is a client-only
change: do not restart pcrd, interrupt other workers, display QA windows, steal
focus or restore the paused finalization performance work.

## Observed production state

Read-only inspection of the installed pcrd 0.1.80 found:

- `pcr-20261009T084034Z-98be6816` was stopped at `08:46:37Z`, finished cancelled,
  separately discarded at `08:47:10Z`, and has no active worker. Its private
  workspace and Codex home were deleted. Agent 1 is cancelled and cannot continue.
- Its input conversation is `01a0c81b-7e07-7b31-a2de-7b18a42d5db3`. The previous
  run `pcr-20261007T103854Z-9f256f08` was finalized and synchronized; session
  promotion succeeded. A read-only `codex.resume_history` request successfully
  read that saved conversation in the correct TunLSCI workspace.
- The competition workspace had a separate active worker
  `pcr-20261009T084330Z-3d524e10`. No operation was issued to it.

These observations establish a saved input conversation, not recoverability of the
discarded private candidate. No real model turn or remote-cleanup command was
submitted during diagnosis or tests.

## Root cause

The result inspector treated candidate continuation permission as permission to
ask any later question. With all candidates cancelled, it incorrectly told the
user to choose another Agent. The composer independently blocked stopped runs
unless they were discarded. For discarded runs it then reused mutable workspace
resume settings rather than the stopped turn's own saved input session.

The baseline regression run failed with 14 issues across four tests: the false
inspector warning, cancelled/stopped/interrupted submission blocks, wrong persisted
and transmitted resume session, and unrelated history borrowed when no saved input
session existed. Running, partial-success review and unresolved-save guards passed.

## Repair

- Classify an idle stopped turn with no reviewable or adopted result separately
  from its deleted private candidate. A later explicit message can start a new
  turn from that turn's saved input conversation.
- Pin both the durable outbox and transmitted settings to the stopped run's input
  session. Never resume the cancelled Agent's private thread or an unrelated
  workspace's session. If no saved input session exists, explicitly start a new
  conversation instead.
- Use the real submission plan in the result inspector. Preserve destination,
  connection, queue, in-flight receipt, running-worker and result-save guards.
- Ignore stale candidate-choice confirmation only when that candidate is no longer
  eligible. Do not issue a selection write or replay any old message.

## Verification and deployment

- The focused regression run passed 7 tests in 2 suites, including five stop-state
  spellings, both stopped and discarded explicit submissions, stale selection,
  missing saved history and destination/connection guards.
- The full guarded client driver passed 367 tests in 43 suites in 201.246 seconds,
  plus protocol-contract, desktop-state and native icon checks. Foreground test
  suites remained disabled; the job used one low-priority build worker.
- Python conversation/release tests passed 32 tests and 7 subtests. Generated
  protocol files remained current; no backend source or protocol change was needed.
- Eight native offscreen screenshots cover saved/new conversation, light/dark and
  standard/extra-large text. The actual inspector renders the stopped Agent and
  correct next-message context without opening any window. The saved/dark/large
  screenshot was visually inspected: no clipping or overlap.
- macOS 0.1.420 (build 420) was built and signed with the existing identity.
  The packaged math/syntax rendering check passed in prohibited activation mode.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/StoppedContinuation-20261009`.

The first maintenance-only exit preflight aborted before any deployment mutation
because it read the detached-worker flag from capabilities instead of health. A
subsequent additional identity check correctly aborted because controller identity
is different from local machine identity. The preflight now reads the live health
flag and the enrolled machine identity from relay status; no safety check was
removed. Both aborts left the installed client and workers unchanged.

Built-in updater acceptance and real installation results follow below only after
those operations actually complete. No real user/model turn is submitted for QA.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T09:50:51.775791+00:00",
  "macos": "0.1.420",
  "archive_sha256": "96e4b9ab98ec31f9b364b8786060eab836c3a1aa44fc57b690f1689ee9326330",
  "relay_catalog_verified": true,
  "pcrd_unchanged": "0.1.80",
  "actual_installed_client": [
    "0.1.419",
    "419"
  ],
  "client_tests": 367,
  "client_suites": 43,
  "python_contract_tests": 32,
  "python_subtests": 7,
  "focused_tests": 7,
  "offscreen_inspector_images": 8,
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T09:52:56.839341+00:00",
  "macos_prepared": "0.1.420",
  "actual_installed_client": [
    "0.1.419",
    "419"
  ],
  "actual_pcrd": "0.1.80",
  "scheduler_pid": 46777,
  "client_not_restarted": true,
  "builtin_integrity_and_signature_verifier_used": true,
  "healthy": true,
  "paired": true,
  "no_qa_window_or_user_input": true
}
```

## Verified Installation

```json
{
  "checked_at_utc": "2026-10-09T09:53:13.519606+00:00",
  "macos": "0.1.420",
  "build": "420",
  "client_pid": 67020,
  "loaded_executable_inode_verified": true,
  "builtin_updater_used": true,
  "background_relaunch": true,
  "pcrd": "0.1.80",
  "pcrd_pid_unchanged": 46777,
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_processes_preserved": true,
  "existing_workers_unchanged": {
    "pcr-20261009T084330Z-3d524e10": {
      "pid": 51759,
      "fingerprint": "51759 Fri Oct  9 16:43:30 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
    }
  },
  "cancelled_cleanup_still_discarded": true,
  "rollback_performed": false,
  "no_qa_window_or_user_input": true,
  "no_real_user_turn_submitted_for_qa": true
}
```
