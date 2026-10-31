# Diff isolation and interactive latency, 2026-10-29

## Scope and outcome

- Worktree: `/Users/mingliangxu/Desktop/parallel-codex-runner`; existing unrelated changes retained.
- User requirements remain: history plus current body within 1 second on navigation; real model response within 10 seconds on follow-up, or after the original queue countdown expires. No model, effort, Fast, countdown, or research task changes.
- This iteration fixes a reproducible backend blocking path. It does **not** establish universal 1-second/10-second compliance.
- pcrd **0.1.131** is active locally; macOS remains **0.1.496**, without a client restart. Local wheel/manifest updated; no remote release-catalog publication claimed.

## Proven defect and repair

`diffing._entry_patch` used `difflib.SequenceMatcher` for large repeated-line files. An 80,000-line fixture required **8.632 seconds**. Large text diffs now use the existing Git histogram implementation outside Python's GIL; compact and full-context patches retain the original captured bytes, path labels, newline semantics, and existing mode/binary handling. Installed-runtime measurement: **0.068 seconds**. Both patches were applied with Git and byte-compared to the expected result, including CRLF and missing final newline.

`PluginRunManager.get_diff` also persisted completed-result diffs under the global manager lock. It now captures the run identity/state under the lock and performs file computation/persistence outside it, as the live-diff path already did. The regression verifies another thread can acquire the actual manager lock during persistence.

The live 129 daemon had a CPU-heavy request thread and widespread delayed reads. Its native stack is consistent with repeated-line matching but does not identify the exact Python request. Health recovered naturally **before** 131 activation, so that recovery is not counted as this fix's causal benefit.

## Validation

- Frozen-source backend suite: **1,339 tests / 358 subtests passed**.
- Installed 131 runtime suite: **314 tests passed** (`installed131-tests.log`).
- State-format v3 interoperation passed with installed 129 and 130 writers.
- Source/wheel/installed package comparison: **324 Python files matched**.
- Wheel SHA-256: `b8c1dd573e9fa73e57814f1fce3ca12d5d0c1c5f2ce5879049066a569e0770f9`.
- Normal detached-manager handoff at 15:53 Asia/Shanghai preserved all **5 original owners and 16 then-live native processes**, their thread/turn/message identities, pending command, and user-stopped states. Subsequent bounded original logs showed new events for 15 of the 16. No progress in one bounded interval is not a failure diagnosis. No task was resent, stopped, or finalized by this handoff.
- New manager PID 24673; local authenticated health 0.016 seconds initially and 0.006 seconds on the final check. Existing Relay bridge reconnected; not a claim about every remote compute node.

## Measured requirements

Production macOS model/renderer, hidden isolated native window, actual local daemon:

- Five workspaces, **28 Agent branches**, 109-231 history entries per workspace.
- Original history ID membership, current cursors, and final body markers checked.
- **0.009-0.557 seconds**, 140 read-only requests, no request errors.
- Renderer cache was cold, but baseline reads had warmed the server. This is not a measurement of input-to-screen presentation in the user's actual window, nor proof of every intermediate paragraph's text/order.

Long-conversation acceptance workspace, actual installed backend and Codex, authenticated RPC entry:

| Case | Agent 1 first text | Agent 2 first text | First complete message |
| --- | ---: | ---: | --- |
| Follow-up | 32.83 s | 11.22 s | 38.94 / 14.92 s |
| Queue after original deadline | 32.21 s | 13.91 s | 33.47 / 14.85 s |

Both cases used the existing 2 candidates, no synthesis, `gpt-6.1-sol`, `max`, Fast off, and original 60-second queue delay. They continued the existing synthetic Event Ledger conversation. Original command journals and native thread/turn/item identities were checked. Both test turns completed; queue is empty. No test input went to a research workspace.

**The 10-second requirement failed.** Direct admission took 1.16 seconds; native `turn.started` was observed at 3.92/4.02 seconds. Queue native turns started at 6.08/6.19 seconds after the deadline. Most remaining delay is between native turn start and first nonempty text, not evidence of provider-only latency. The observer sampled original native stdout every 100 ms, not projected UI state. No receipt or thinking animation was counted as a reply.

## Remaining acceptance / investigation

- Actual-window subsecond timing is not signed off. AX reads timed out; a 2-second sample found 120/121 main-thread samples in the ordinary event wait. A subsequent screenshot succeeded and showed a real unsent draft, so navigation was deferred until the user had sent it. Later read-only navigation from compression to memory, then Agent 1 to Agent 2, visibly displayed the correct history/current text and tools. Original Agent selections and compression workspace were restored. The Agent click call took 0.511 seconds, but the screenshot call itself took 10.209 seconds, so these observations cannot establish first-paint latency or a 1-second pass. AX timeout alone is not a client-freeze diagnosis. No client restart, message submission, result adoption, or worker control was used for this UI check.
- Existing `thread/resume` already uses `excludeTurns=True`. Measured queue history preparation was 0.027 seconds, private materialization 0.004 seconds; do not repeat this optimization or replace native history with summaries.
- Next useful measurement is raw native protocol arrival versus adapter emission versus actual-client first visible text, preserving the same message identity. Do not invent a warm executor pool, reduce model settings, force early acknowledgements, or change the SLA clock without evidence and authorization.
- Completion of this iteration is not completion of the broader performance requirement.

## Evidence

Private records: `~/Library/Caches/ParallelCodexMaintenance/SidebarGap-20261029/`:
`performance-resume-baseline.json`, daemon/client samples, `131-switch-*-*.json`,
`live-native-after131.json`, `backend131-active.json`, `backend131-final.json`,
`acceptance131-submission.json`, `acceptance131-measurements.json`, source hashes,
wheel/installation/interoperation records, and installed-runtime test log.

Native render measurements and bounded synthetic native observations:
`~/Library/Caches/ParallelCodexMaintenance/InteractivePerformance-20261027-39zHuL/live-navigation-backend131/`
and `acceptance-native-observation-v131followqueue.jsonl`.
