# macOS Account Usage Scope

## Cause

`SidebarCodexUsage` incorrectly combined the machine ID and selected workspace ID
into the quota cache and polling identity. On navigation, the first render rejected
the previous account snapshot; the new polling task then cleared it and requested
usage again. Consequently the label briefly became `Usage` even on the same
computer and account. Its request also unnecessarily selected a workspace-specific
Codex binary despite the endpoint reading the computer's account home.

## Repair Boundary

Use the actual computer request target for quota display and polling, and request
the device/default Codex account without a workspace ID. Same-account refresh and
offline retry retain the last successful snapshot. An available new-account
snapshot replaces the previous snapshot without an intermediate empty publication.
Machine changes, account identity changes without a usable value, sign-out, and
invalid pairing still invalidate the old data; old machine responses cannot win.

No backend, task submission, adoption, queue, finalization, or research files change.
Only the macOS component is built and published. Actual publication and installation
evidence will be appended below after verification, not inferred from this plan.

## Native Regression Acceptance

With the previous workspace-dependent scope and empty account-transition publication,
both new root regression tests failed (104 expectations). After the repair, all 12
quota tests passed, including machine switching with a delayed old response, account
change, sign-out, offline retry, reset-time labeling, and workspace-free request encoding.
The complete client gate passed 433 tests in 50 suites. Python usage, protocol and
release gates passed 62 tests and 7 subtests. SDK: macOS 26.5, native SwiftPM, no
interactive QA authorization.

The actual `SidebarCodexUsage` was hosted in a non-visible AppKit window. Across 12
workspace changes, 48 rendered PNG frames matched the initial nonblank quota image
byte for byte; cache context and polling identity remained unchanged, and no context
publication occurred. The test fixture used a missing IPC endpoint to ensure a slow
or unavailable refresh could not mask the navigation regression. The resulting
light-mode image was visually inspected and displayed `73% left` with the gauge.

## Verified Publication

```json
{
  "checked_at_utc": "2026-10-09T19:53:46.633048+00:00",
  "macos": "0.1.428",
  "archive_sha256": "516f05c2ef508cf07b4f897cf3734d5e326f9001660e1251b5948cc6935ba9ea",
  "relay_catalog_verified": true,
  "actual_installed_client": [
    "0.1.427",
    "427"
  ],
  "client_tests": 433,
  "client_suites": 50,
  "python_contract_tests": 62,
  "quota_navigation_frames": 48,
  "workspace_switches": 12,
  "test_sdk": "MacOSX26.5.sdk",
  "foreground_qa": false
}
```

## Verified Preparation

```json
{
  "checked_at_utc": "2026-10-09T19:54:15.718736+00:00",
  "macos_prepared": "0.1.428",
  "actual_installed_client": [
    "0.1.427",
    "427"
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

## Verified Actual Installation

```json
{
  "checked_at_utc": "2026-10-09T19:57:00.062007+00:00",
  "macos": "0.1.428",
  "build": "428",
  "client_pid": 88650,
  "loaded_executable_inode_and_hash_verified": true,
  "builtin_update_lifecycle_used": true,
  "foreground_app_was_already_pcr_before_update": true,
  "background_relaunch_claimed": false,
  "ordinary_quit_not_used": true,
  "actual_pcrd": "0.1.80",
  "scheduler_pid_unchanged": 46777,
  "worker_and_node_pids_and_start_times_preserved": {
    "33933": "Sat Oct 10 00:58:34 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "33987": "Sat Oct 10 00:59:08 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "40479": "Sat Oct 10 01:22:35 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "46777": "Fri Oct  9 16:04:19 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "68341": "Fri Oct  9 18:00:16 2026     /opt/homebrew/Cellar/node/26.0.0/bin/node"
  },
  "outbox_and_followup_queue_preserved": true,
  "persisted_drafts_preserved": true,
  "active_runs_preserved": [
    "pcr-20261009T165829Z-72e0a5ca",
    "pcr-20261009T172232Z-d47fdb49"
  ],
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_http_status": 200,
  "rollback_performed": false,
  "no_user_turn_submitted_or_result_adopted": true
}
```

## Installed Client Navigation Acceptance

CUA read the actual installed 0.1.428 client after the built-in update. The original
hand-decoding workspace was switched to memory decoding, then epidural decoding,
and back to hand decoding. Four full AX snapshots (initial plus three selections)
all reported `Codex usage - 72% left` in Chinese for `sidebar.codex-usage`. The
original Agent 1 selection, results inspector, sidebar position, and unsent draft
were restored. No result was saved, adopted, discarded, stopped, or resubmitted.
These formal-client AX snapshots verify the live user path; the per-frame flicker
regression is verified separately by the non-visible native rendering test above.

The installed executable's mapped inode and SHA256 match the staged signed build.
Actual runtime remains pcrd 0.1.80, scheduler PID 46777. All three existing worker
PIDs and the Node PID/start times are preserved. An epidural run finished naturally
before installation; the two runs active at the immediate install preflight remained
active afterwards. Pending outbox entries, full persisted draft/outbox hashes,
authoritative server follow-up queues, and recorded result choices match the
immediate preflight.
Outbox comparison is by message ID, not dictionary iteration order; the first
order-sensitive verification assertion was corrected without modifying runtime data.

A later strict comparison of all persisted queue cache bytes failed after workspace
navigation. Investigation confirmed that the only changed key was
`followUpQueuesByContext`: the existing daemon reconciliation removed cached entries
after reading the already-empty authoritative queues. Every server queue, including
its generation and timestamps, remained exactly unchanged. Drafts, outboxes and
result choices also remained unchanged. The post-navigation verifier explicitly
checks these authoritative records and permits this cache-only change only when
the preflight server queues and the current local cache contain no items. It does
not restore stale cache data or replay any messages. Installation verification
before navigation passed the original full-cache equality check.

## Verified Post-Navigation State

```json
{
  "checked_at_utc": "2026-10-09T20:03:58.087863+00:00",
  "macos": "0.1.428",
  "build": "428",
  "client_pid": 88650,
  "loaded_executable_inode_and_hash_verified": true,
  "builtin_update_lifecycle_used": true,
  "foreground_app_was_already_pcr_before_update": true,
  "background_relaunch_claimed": false,
  "ordinary_quit_not_used": true,
  "actual_pcrd": "0.1.80",
  "scheduler_pid_unchanged": 46777,
  "worker_and_node_pids_and_start_times_preserved": {
    "33933": "Sat Oct 10 00:58:34 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "33987": "Sat Oct 10 00:59:08 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "40479": "Sat Oct 10 01:22:35 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "46777": "Fri Oct  9 16:04:19 2026     /opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
    "68341": "Fri Oct  9 18:00:16 2026     /opt/homebrew/Cellar/node/26.0.0/bin/node"
  },
  "outbox_and_followup_queue_preserved": true,
  "persisted_drafts_preserved": true,
  "authoritative_server_queues_unchanged": true,
  "empty_server_queue_cache_refreshed": true,
  "active_runs_preserved": [
    "pcr-20261009T165829Z-72e0a5ca",
    "pcr-20261009T172232Z-d47fdb49"
  ],
  "healthy": true,
  "paired": true,
  "gpt_6_1_sol_available": true,
  "node_http_status": 200,
  "rollback_performed": false,
  "no_user_turn_submitted_or_result_adopted": true
}
```
