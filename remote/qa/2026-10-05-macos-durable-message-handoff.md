# Durable Message Handoff, 2026-10-05

## Scope and Authorization

The user requested a root fix for messages stuck after the preceding turn saves its results, not wording changes or forcibly enabling Send. The user authorized macOS publication, installation and background restart after verification, and pcrd activation only when all candidate/synthesis Agents and finalization/other operations are idle. No foreground QA, focus changes, physical input, Android interaction or task cancellation/resubmission is authorized.

Baseline independently read from the installed client and service: macOS 0.1.413/build 413, pcrd 0.1.71, healthy and paired. On the deployment snapshot all active_runs were empty and active_operation was null.

## Observed Cause

Original command: `a8994edd-1643-4bb9-b561-2e6a746aa6a5-attempt-0-confirmed`, client `mac-app`, continuation from `pcr-20261004T162708Z-9104f9b6`, Agent 8. Fingerprint: `593035a00fefe445281929390f98d51796ab8d69a607f4b786bc41f052ce90aa`.

All eight Agents finished at 00:47:58 CST. Finalization started at 01:14:09. The confirmed message entered at 01:14:56, while diff preservation had not yet populated finalized_agent. The old continuation path checked pending finalization only within its already-finalized branch. Its adoption path classified the saving worker as active Agent execution and called stop_run, which timed out. The command journal retained a generic failed/unknown-outcome record. Finalization actually completed at 01:16:41, but that record could not progress afterward. The client correctly avoided blind duplicate execution, but its unresolved outbox then blocked the next draft in the same workspace.

This is an operation classification and delivery ownership failure, not merely slow networking or a misleading banner. The old journal also held a thread-global lock across callbacks, delaying unrelated workspaces behind expensive mutations.

## Implementation

- Separate ongoing result saving from Agent execution before adoption, continuation and stopping. Add finalizing_agent as intent; do not prematurely claim finalized_agent or synced completion.
- Keep the existing per-Run operation lock while moving expensive artifact preservation outside the manager-global lock. Re-read committed Run state before continuing its local finalization; retain other workspaces' Run/command records.
- Persist the full original request in the existing command journal before attempting execution. Typed, definitely-unaccepted pending requests are advanced by a bounded background coordinator, restored from the journal at startup. Unknown failures are not automatically executed.
- Use the same per-workspace prompt admission and latest-context validation for direct messages, ordinary follow-ups and recovery. Follow-up messages remain owned by the existing follow-up store, not both coordinators. Different workspaces no longer share one long journal callback lock.
- Keep GET receipt queries read-only. Retain pending records beyond ordinary receipt retention; keep lock-file identities stable. Recheck terminal index cleanup under the command lock so a stale observation cannot erase a newer pending attempt.
- Client follows daemon-owned receipts without repeating the submission, retains draft/source identity, and durably associates the accepted Run before releasing transport recovery. Destination hydration remains a distinct state; Send is not forcibly unlocked against an old source.
- For this exact legacy stop-timeout only, require committed finalization of the same Agent, successful sync/session promotion and no successor before granting safe original-identity recovery. A new started attempt consumes that legacy qualification. Other generic timeouts remain conservative.

## Verification and Evidence

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/DurableHandoff-20261005`.

Four mechanism regressions fail against the independently installed 0.1.71 baseline. The private integration test holds actual diff preservation, obtains a pending receipt without waiting for preservation, then makes no further client submissions. After releasing preservation, the coordinator creates one original-identity continuation and its private candidate writes a file. It asserts no stop_run, no premature start, correct source/session, and survival of an unrelated committed Run. This proves the handoff mechanism, not real-model latency.

The client receipt test verifies multiple daemon-owned polls without a second submit, preservation of the following draft, durable acceptance independently of destination loading, and subsequent binding of that destination. All client validation is headless/offscreen. Swift builds/tests use one job and low priority.

Publication, installed-runtime counts, actual service activation and real original-message execution are recorded below only after independently verified. The initial candidate versions were macOS 0.1.414/build 414 and pcrd 0.1.72.

## Scheduler Activation 0.1.72

```json
{
  "pcrd_version": "0.1.72",
  "healthy": true,
  "active_runs_before": [],
  "active_runs_after": [],
  "supports_prompt_receipts": true,
  "supports_visual_file_comparison": true,
  "gpt_6_1_sol_available": true,
  "controller_paired": true,
  "baseline_client_and_node_processes_preserved": false,
  "changed_baseline_pids": [
    "41845"
  ],
  "scheduler_restarted": true,
  "client_restarted": false,
  "foreground_before": 38541,
  "foreground_after": 38541,
  "supersedes_staged_runtime": "0.1.71",
  "supports_durable_prompt_handoff": true
}
```

## Final Build and Installed Verification

| Check | Actual result |
| --- | --- |
| Installed 0.1.71 mechanism baseline | Four intended regressions failed |
| Focused final Python regression | 323 passed, 38 subtests passed |
| Full final source Python suite | 933 passed, 104 subtests passed |
| Canonical installed 0.1.72 runtime | 932 passed, 1 repository-relative test deselected, 104 subtests passed |
| Headless/offscreen Swift client suite | 330 tests in 39 suites passed |
| Native icon and packaged math/syntax resources | Passed |
| Final release application and installed application signature | Strict verification passed |
| Published macOS catalog and downloaded artifact | 0.1.414/build 414 and exact SHA verified |

The final wheel SHA-256 is `3a5d344def5a55f4172e8db0341ba25120bbeb775479391801a357b8900f783a`. The canonical runtime is `~/Library/Application Support/parallel-codex-runner/runtime/0.1.72`. The pre-review wheel/runtime are quarantined in the evidence directory and were never enabled. The final wheel matches every Python source file; no running private Agent configuration was changed. Unrelated Android and relay release artifacts were preserved.

## Actual Publication and Activation

The signed macOS 0.1.414/build 414 release was published, authenticated-download verified, and installed through the built-in update verifier/installer. Installed executable SHA-256: `9a588bd8d8bb923ac5d1e6be71ed8855b9fa5dcb99e50c7629aed2872f5ba665`. Background relaunch explicitly used `activateOnRelaunch=false`. The formal client PID changed to 41835; pcrd remained 0.1.71/PID 48316 throughout client installation.

Only after repeated globally idle health checks and a successful read-only GPT-6.1 Sol catalog preflight was the scheduler switched once to the canonical 0.1.72 runtime. Independently rechecked actual scheduler: 0.1.72/PID 41934, healthy, paired, detached workers, GPT-6.1 Sol available, prompt receipts, visual comparison and durable prompt handoff enabled. No rollback or additional restart was performed.

Foreground PID was 38541 before and after both the client update and scheduler switch, and remained 38541 in final verification. No QA app/window, screenshots, physical input or focus-taking action was used. All 13 Node processes from the independent pre-install snapshot, including the existing Node web services, retained their PIDs and start times; the updated formal client survived the scheduler switch without another restart.

The stricter immediate activation process-snapshot assertion did fail for PID 41845, a process absent from both the independently saved pre-install and post-client snapshots that exited during the scheduler switch. Its command/parent were not captured and a bounded process-specific unified-log query returned no entries. Its identity and cause are not inferred. The original failed assertion and `baseline_client_and_node_processes_preserved=false` record are retained above. The separate final audit verifies the independently recorded long-lived Node/client processes, not survival of every transient process. Evidence: `final-deployment-verification.json`.

## Original Message Actually Resumed

The formal client automatically recovered the original message under command `a8994edd-1643-4bb9-b561-2e6a746aa6a5-attempt-0-confirmed`, unchanged full fingerprint, original source Run, Agent 8 and promoted session. The observer issued only `commands.get`, `runs.get` and other read-only checks, never a task submission or edited outbox.

The original receipt now reports completed with exactly one associated successor, `pcr-20261005T020818Z-01105da7`, created at 10:08:18 CST. Persistent business state verifies the exact original command identity, source Agent, prompt and settings. Formal client outbox is empty for this original submission and the accepted result has been adopted, not merely hidden.

All five candidate Agents actually started (10:08:34 through 10:08:55 CST), while later candidates were still preparing. Actual command output from Agent 2 was observed at 10:09:18 CST; the continuation is working, not merely a pending receipt or created Run. Its research and three synthesis Agents are ongoing, not claimed complete. No additional submission identity or duplicate successor exists. Evidence: `original-message-recovery.json`.
