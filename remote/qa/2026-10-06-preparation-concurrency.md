# Agent Preparation Concurrency

## Change

- Remove the fixed two-Agent preparation semaphore, without replacing it with
  another automatic preparation limit.
- Keep the user's configured overall Agent parallelism and explicit serial mode.
- Start each prepared Agent independently; do not add an all-Agent barrier.
- Keep one shared history snapshot and independent writable Agent environments.
- The same scheduler path serves initial, additional, retried and synthesis Agents.

## Focused Verification

- The five-simultaneous-preparations regression fails against installed 0.1.77.
- Modified source: 58 tests and 9 subtests passed in 2.67 seconds.
- Coverage includes five concurrent preparations, configured parallelism of three,
  serial execution, Agent 5 starting while Agents 1-4 are still preparing,
  additional Agents, isolated source files, shared instructions resolved once,
  preparation failure and retry, and cancellation joining in-progress copies.
- These are isolated local-process tests, not model submissions or a performance
  speedup claim. No QA windows or foreground interactions were used.

## Deployment Boundary

- At the first live check, formal pcrd remained 0.1.77 and two user runs were active.
- No running task, client, Node service or private task configuration was changed.
- A new wheel and independent runtime are not considered enabled until a globally
  idle, compatible-client activation and live verification succeed.
- The previously paused heartbeat remains paused; do not create another polling
  automation for this change.

## Full Verification

- The first full source run found one release-metadata mismatch: the plugin
  manifest still declared 0.1.77 while the package declared 0.1.78. After aligning
  package, plugin and root lockfile versions, the full suite was rerun.
- Source: 1001 tests and 120 subtests passed in 88.63 seconds.
- Installed 0.1.78 runtime: 1000 tests and 120 subtests passed in 92.96 seconds.
  Only the repository-source-location check was deselected; it passed in the
  source run. No functional scheduling tests were excluded.
- The immutable wheel differs from 0.1.77 only in app.py. AST comparison confirms
  that changes there are confined to run_all_agents and removing the preparation
  limit constant. Native history, recovery, protocol and state implementations
  are unchanged; their previous native acceptance is reused, not represented as
  a new 0.1.78 native-model run.
- Every installed Python module was compared with the published wheel. The
  existing signed macOS 0.1.417 client was checked without opening or restarting it.
- Installed CLI smoke: `pcr --version` returned `pcr 0.1.78`.

## Activation Deferred

The audited activation script returned 75 at 2026-10-06 02:28:44 UTC because
these original user runs remained active:

- pcr-20261006T021629Z-4c1ee76d
- pcr-20261006T021719Z-9914e888

Formal pcrd remained healthy and connected at 0.1.77. Neither the scheduler nor
the client was restarted. Version 0.1.78 is published and installed in an
independent runtime, but its preparation change is not yet active in production.
The existing heartbeat remains paused; no replacement automation was created.

## Publication

```json
{
  "published_at_utc": "2026-10-06T02:28:43.153706+00:00",
  "pcrd_version": "0.1.78",
  "sha256": "ed9063e66c95bc9848a4f8bbd2f434b34bf0112c6050d6fe30c27ff3faf1b5b7",
  "actual_version": "0.1.77",
  "scheduler_activated": false,
  "client_restarted": false,
  "qa_windows_opened": false
}
```
