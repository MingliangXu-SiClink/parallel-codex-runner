# Composer handoff and false-blocking repair

## Scope

Target: pcrd 0.1.88 and macOS 0.1.430 (build 430). Publication, staging,
installation, and actual runtime activation are separate acceptance stages.
No existing research turn may be submitted, stopped, adopted, discarded, or retried by QA.
Activation and client restart require repeated global idle checks, empty message
admission/delivery/follow-up queues, and a safe official-client exit.
Durably saved client outboxes and accepted handoff markers are preserved for the
new version to reconcile, not required to disappear first. Otherwise the repair
could be prevented from installing by the very stale receipt it must fix.

## Entry-Point Audit

The audit covers five composer sources, historical-conversation footer replacements,
and conversation connection/read notices. They represent these business paths:

| Path | Root handling and acceptance |
| --- | --- |
| Navigation binding | Freeze original machine/workspace/conversation/source Agent; late results cannot select a different conversation or clear a new draft. |
| Local draft/outbox | Persist before sending; retain original command when local writes fail; restart reads the same saved identity. |
| Workspace outbox ownership | Reconcile accepted commands independently of which workspace/conversation/Agent is displayed. |
| Send and receipt | Keep one immutable command; reconcile delayed or lost acknowledgements without a second logical message. |
| Accepted handoff | Validate destination provenance, persist a restart-readable destination, then atomically consume only that command's receipt. |
| Other workspace turns | Temporary proven-not-applied busy preflight remains daemon-owned and progresses after the workspace becomes idle. |
| Continuation Agent | Browsing another Agent does not change the immutable submitted source or strand its acknowledgement. |
| Sync configuration | Genuine adoption prerequisites and configuration conflicts remain enforced; no forced sync. |
| Finalization/save/cleanup | Preserve real filesystem/conflict protection; this release does not resume the deferred finalization-performance redesign. |
| Control operations | Verify actual postconditions after a lost reply; never repeat a stop/adopt/discard action to dismiss an error. |
| Capacity | Real capacity confirmation remains explicit; no bypass or cleanup of research data. |
| Queue editing | Reconcile exact removal/reorder postconditions after lost replies, preserving concurrent appended messages and generation conflicts. |
| Queue dispatch | Keep daemon ownership and original message/settings; do not resurrect removed, paused, or already sent messages. |
| Connection/history/artifacts | Read recovery cannot change message identity, imply completion, or hide a real permission/authentication failure. |

## Fault Acceptance

Use real local socket transport, on-disk restart state, and isolated managers.
Cover fragmented/slow replies, late acknowledgement after a client lifecycle change,
rapid conversation switches, cold cache, failed durable writes, incorrect destination
provenance, queue reply loss plus concurrent enqueue, legacy journal ownership, and
server restart while the same message waits for a workspace. Assert exact prompt,
source, command ID, number of logical executions, queue contents, draft preservation,
and independent handoff completion, not merely disappearance of notice text.

An unpublished 0.1.87 candidate was rejected during compatibility review. It
used `workspace_busy` for a daemon-owned pending prompt, which older clients
could misclassify as editable rejection. The final backend uses the established
`pcr_operation_pending` wire state for this temporary preflight, retaining the
same immutable command and keeping real unsafe-sync errors rejected. The old
candidate wheel/runtime is not in the release catalog and must not be activated.

An authenticated, still-present retryable receipt is a positive proof for the
exact original command. It can settle a legacy preflight without fabricating or
refreshing its missing/old first-attempt date. Absent, ambiguous, corrupt, or
identity-conflicting receipts retain their existing replay-window safeguards.

Full source/runtime/client checks, immutable-artifact validation, signed packaged
resource checks, deployment gate tests, publication, and activation evidence are
appended below when each stage actually passes.

## Verified macOS Publication

```json
{
  "checked_at_utc": "2026-10-14T17:51:54.468573+00:00",
  "macos": "0.1.430",
  "archive_sha256": "ca8e97c6913f75fc2a550f35e871ce93c3aa8d799f32cf049c5d47322e1b534d",
  "relay_catalog_verified": true,
  "client_installed_or_restarted_by_publication": false,
  "client_tests": 452,
  "client_suites": 51,
  "foreground_qa": false,
  "message_identity_and_restart_fault_regressions": true
}
```

## Published Backend

```json
{
  "checked_at_utc": "2026-10-14T17:52:13.493651+00:00",
  "pcrd": "0.1.88",
  "wheel_sha256": "3b4233e08fd4facba85d7a65829379638f7f311445b71a229b602f287174f55e",
  "other_artifacts_preserved": true,
  "macos_unchanged": true,
  "scheduler_activated": false,
  "remote_jobs_launched_by_qa": false
}
```

## Verified Fault Acceptance And Deferred Installation

```json
{
  "checked_at_utc": "2026-10-14T17:54:45.572204+00:00",
  "published_macos": "0.1.430",
  "published_pcrd": "0.1.88",
  "source_tests": "1105 passed, 216 subtests passed in 101.29s (0:01:41)",
  "installed_tests": "1104 passed, 1 deselected, 216 subtests passed in 103.23s (0:01:43)",
  "client_tests": 452,
  "strict_pool_fault_tests": 26,
  "deployment_gate_tests": 24,
  "native_loopback_cases": 6,
  "real_user_turn_submissions": 0,
  "actual_macos": [
    "0.1.429",
    "429"
  ],
  "actual_pcrd": "0.1.86",
  "healthy": true,
  "paired": true,
  "active_runs": [
    "pcr-20261014T150143Z-3647dc05",
    "pcr-20261014T150350Z-dfc27db5",
    "pcr-20261014T153618Z-8e363464",
    "pcr-20261014T154150Z-066822c9"
  ],
  "client_and_scheduler_not_restarted": true,
  "baseline_client_and_node_processes_preserved": true,
  "changed_baseline_pids": [],
  "follow_up_id": "pcr-0-1-88-430",
  "follow_up_stops_after_actual_installation": true,
  "website_collaboration": "Existing embedded-browser conversation returned cannot-load-reply; no partner response was used as evidence"
}
```
