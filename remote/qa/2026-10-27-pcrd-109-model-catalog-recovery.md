# pcrd 0.1.109: Restore the Actual Model Catalog

## Incident and Confirmed Defect

Evidence status: functional recovery and the negative-cache defect repair are
verified. Attribution of the original live incident, including its first
failure, remains open. These are separate completion states; 109 must not be
reported as proof that the historical incident's complete root cause was found.

The user's criticism of 478 was valid: removing the virtual default and showing
an honest discovery error had not restored usable model selection. The live
108 `codex.model_options` API still failed in approximately 0.36 seconds.
A fresh process using the same installed library, daemon environment, working
directory, CLI 0.159.2, Codex home and account context obtained the native
`model/list` catalog in approximately 2.14 seconds. No inference was submitted.

`CodexModelCacheManager._version_locked` cached a failed version probe as an
empty string and treated that value as a permanent cache hit for an unchanged
executable. An isolated regression fails against the old code. This proves the
defect, but the old live process's internal cached value and the first failing
probe's original stack were not obtained; replacing the manager does not prove
that this was its only failure path.

A follow-up source review also confirms that multiple catalog failures are
collapsed to `None`, and native catalog stderr is discarded. The uniform API
error therefore cannot identify whether a failed request stopped at version
discovery, locking, support-file preparation, native RPC or decoding. An
isolated test additionally showed that a fresh valid catalog read waits for the
refresh file lock. Neither observation establishes which path the old live
incident took; neither is being substituted for that missing causal evidence.

## Minimal Repair and Verification

- Failed version discovery retries after a 30-second monotonic backoff in the
  same manager. Reads during backoff do not extend its deadline. A successful
  version stays cached; replacing the CLI bypasses the old negative backoff.
- CLI/account/config catalog partitioning, freshness, hidden-model filtering,
  execution, authentication and message identities are unchanged. No other
  CLI's shared cache was substituted, retagged or deleted.
- 176 affected tests and 28 subtests passed. Coverage includes an initial
  failure followed by recovery through real `PcrdServer`/`PcrdClient` and the
  original controller/manager instance, not just a helper. The fake native
  service receives only initialize/initialized/model-list calls and no run.
- The installed 109 wheel independently passed 19 unittest cases, including
  persistent-worker execution across manager shutdown, subsequent completion
  and later operations. `pip check` passed. All dependency versions were fixed
  to those of the existing 108 runtime; only the PCR package version changed.
- Every Python module in the wheel was compared with installed 108: only
  `parallel_codex_runner_core/codex_model_cache.py` differs. Its installed SHA-256
  is `6982e1b40069cf424d96d890fd7ef90064a7d164650f3062ff9fe04aa3ae6222`,
  identical to source. The first integration-test attempt hit macOS's Unix
  socket path limit; a private short test socket fixed that fixture, without
  changing production paths. Initial failed and final successful logs remain.

## Safe Activation and Original Execution

The first activation attempt deferred before changing anything because a new
user task was active. After verifying the existing detached-worker shutdown
contract, independent process groups, durable receipts, zero manager operation,
zero admission/delivery and no queued follow-ups, only the manager was retired
normally. Old PID 74324 fully exited before 109 was bootstrapped. Only the
LaunchAgent interpreter path changed, to its dedicated 109 runtime.

Live manager: PID 5118, started 2026-10-27 16:10:10 Asia/Shanghai, reporting
0.1.109 through capabilities. The macOS 478 process remained PID 98281; the
client was neither replaced nor restarted. Relay was not redeployed.

The original active run `pcr-20261027T080538Z-fcfe0f1d` retained worker PID 4065,
native PID 4134, original workspace, input/config hash, thread, turn, message ID
and recovery identity. Its recovery count remained zero. Subsequent bounded
native reads contain new command-start, update and completion events, not just
an alive PID or accepted input. The earlier run observed at preflight completed
and finalized before activation; it was not stopped or finalized by this fix.

All seven older persistent workers, the original user-stopped Agents 10/11,
their historical event-file identity/tail, the unresolved stop journal and the
follow-up queue hashes were unchanged. Protected persisted client settings,
drafts, outboxes, accepted handoffs, local queues and answer-store fields were
also unchanged. No original input or pending stop was replayed. Original worker
runtimes remain installed; no user data was rolled back or cleaned.

## Live Functional Acceptance

At 16:10, the actual 109 API returned the native catalog for both the explicit
candidate and synthesis model contexts, in approximately 0.78 and 0.19 seconds.
At 16:15, the same long-lived manager returned both again in approximately
4.47 and 0.33 seconds, including a real catalog refresh beyond its normal
freshness window. Ten native records contain eight picker-visible models and
two hidden records; the visible IDs match the API and installed client.

The unchanged installed 478 candidate menu was actually opened: eight concrete
choices, preserved Astra selection, no discovery error and no virtual default.
Screenshots and native accessibility evidence were saved. No other model/count
was selected and no test message was sent. The current run's synthesis controls
are legitimately locked at zero; they were not unlocked for QA. Its catalog
API was checked separately, so no unsupported claim of clicking that locked
menu is made. The original client window and active execution were retained.

## Artifact and Boundaries

Local immutable release artifact and manifest entry:
`release/pcrd/parallel_codex_runner-0.1.109-py3-none-any.whl`, 1,190,311 bytes,
SHA-256 `c2e27ede638612331abc02c503fe4658c4b7425c86118e7d01151f384069d12e`.
All manifest artifact hashes/sizes were verified; the macOS 478 signed archive,
Android artifact, installer and Relay 21 entries were preserved. This is an
installed pcrd repair, not a new macOS binary, shared-plugin upgrade or claim of
an additional server-side wheel publication. No re-enrollment occurred.

Private evidence: `~/Library/Caches/ParallelCodexMaintenance/ModelCatalog-20261027-8dDIbD/`.
Keep tests, activation/readback receipts, exact patch and UI proof; remove only
this incident's frozen build source and duplicate wheel staging. Independent
research jobs and datasets were not inspected or manipulated for catalog QA.
