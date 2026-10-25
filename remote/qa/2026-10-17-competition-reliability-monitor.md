# Competition Reliability Monitor

## Authorization And Schedule

The user explicitly requested hourly PCR process/Agent monitoring, root-cause repairs, safe releases/activation and original-session recovery of unexpectedly interrupted work without disturbing the competition.

- Created and read back automation **`pcr`**, named `PCR 比赛任务每小时巡检与安全恢复`.
- Kind: local thread heartbeat. Status: **ACTIVE**, interval: **60 minutes**, continuing in this chat rather than creating new tasks.
- Existing version-specific activation and capacity-follow-up automations remain paused. None were re-enabled or repurposed.
- This is app-scheduled local work, not a new independent system LaunchDaemon or a cloud monitor. The Mac and desktop app must remain running and the project must stay accessible. It cannot promise continuous observation while the machine sleeps or the app is closed.

## Action Policy

Each pass verifies actual enabled/loaded versions, service/relay status separately, live owner identity and start time, candidate and synthesis progress, messages/receipts, accepted handoffs, local/service queues and agent questions. It compares bounded snapshots and investigates actual changes rather than rereading all private histories.

Confirmed software faults authorize scoped PCR fixes, relevant regressions, immutable packaging, safe installation and actual loaded/UI/native-behavior acceptance. Recovery retains the original run, physical Agent, working directory, native history/session, user input, parameters and completed work. Accepted/uncertain requests must be reconciled before any action; no blind retry or replacement message identity. Normal completion, waiting, long tools/calculations and user-requested stops are not failures.

Healthy competition work must not be stopped or restarted to install updates. A verified isolated controller switch or targeted failed-owner recovery may be used only at its proven safe boundary with the existing locks and ownership protections. A normal client restart requires safe quit semantics and preservation of all real open-window drafts, questions, outboxes and queues. No force kill, foreground QA, keyboard simulation, signing, keychain/security changes, research-file cleanup, automatic adoption or repeated user turn is authorized by this monitor.

Normal/unchanged observations remain quiet. Notify only meaningful new faults, completed repair/recovery, or a specific blocker requiring the user. Do not claim that every interruption is necessarily a PCR bug; first establish whether the cause is internal, a deliberate stop, provider/network, credentials, storage or permissions.

## First Actual Pass

Read-only snapshot: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/CompetitionWatch/snapshot-20261016T163527.249799Z.json`, captured 2026-10-17 00:35 local time.

- Actual pcrd **0.1.98**; actual loaded macOS **0.1.439/PID 64075**, executable inode matched the installed file.
- **4 active competition task groups**, all with live owners and no reported run/worker error.
- **8 persistent worker owners** alive, including the four active groups and retained idle owners. This is not eight simultaneously active task groups.
- Agent readback: fine-hand 8 running; movement across days 10 success and 1 running; compression 8 running; memory-state 8 running. Total **25 running / 10 successful**, no failed/cancelled/interrupted Agent in these active groups. Native activity includes tools, thinking and intentional timed waiting; none was killed because of low output.
- Main-window old outbox count **0**, accepted-handoff count **0**. The separate new user message remains queued once, unpaused, with its original destination and prompt hash. No additional open-window state needed inspection in this pass.
- Local management health is `ok`, pairing retained, no active management operation, admission 0, pending/active service delivery 0. This is not proof of relay availability.
- **Unresolved distinct issue:** relay bridge and credential renewal still report HTTP 502 with link `connecting`. Bounded stderr readback confirms repeated retries. The monitor prompt explicitly prioritizes diagnosis of this issue without restarting local competition workers, lowering certificate validation or pretending an unverified code change is a fix.
- This first pass did not restart, stop, recover, adopt, resubmit, install or release any application/task. No fresh software release was needed for the observed healthy active-Agent state; the relay fault is not claimed solved.

The private `snapshot.py` uses the actual LaunchAgent-selected installed runtime, rejects a mismatched interpreter/package, makes only read RPCs and writes compact private evidence. It is an observation helper, not an autonomous restart script and not a substitute for investigating a real fault. Its first live pass and schedule/state readback checks passed.

## 05:33 UTC Legacy Synthesis Recovery

The fine-hand run `pcr-20261016T044140Z-9d65cdfe` disappeared from active runs after its eight candidates completed. A targeted `runs.get`, rather than treating that disappearance as success, found physical synthesis slots 9/10/11 in preparation errors. The original idle owner **77777** still loaded **0.1.92**. Upgrading the global daemon earlier had deliberately not interrupted or replaced that running batch's pinned executor.

The three errors were the already-fixed Codex history contract failure for `guardian_review_feedback` and `thread_read_receipts`. All three slots had empty commands, no native thread/turn/receipt/log, no prepared workspace and an empty private home. Their original input projection and artifact fingerprints matched. This was a confirmed pre-submission preparation failure, not an uncertain accepted turn and not a user stop.

The existing published **0.1.98** wheel and installed package were reverified byte for byte, including the original source/installed test and native-acceptance report hashes. A fresh installed-runtime regression passed **45 tests**. Fresh isolated native acceptance completed **three original-history resumes and three synthesis executions**, preserved eight synthetic candidates and a neighboring background process, and sent zero real user turns.

The existing recovery CLI is deliberately pinned to its original validated 0.1.97 release. A private explicit 0.1.98 maintenance adapter reused its unchanged fingerprint inspection and retired-owner observer plus the installed manager's existing operation lock, idle checkpoint, publication gate and late-write protection. It did not weaken that CLI's version check or change any published wheel/runtime. An exclusive attempt record prevents blind repeat dispatch.

After fresh message/window/owner checks, the idle owner retired and the original daemon observed and reaped it before the replacement was published. Exactly one recovery dispatch started **0.1.98 owner 68710**, operation `pcr-20261016T044140Z-9d65cdfe-01792215587476563000-79e198bede`, for the same physical synthesis slots **9/10/11**. The eight candidates were not rerun, adopted or copied back.

At **05:42:40 UTC**, actual recovery acceptance verified:

- Three distinct native accepted turns in the original thread `01a12134-86ee-73d1-b0bc-74b3654f5a2b`, each with new model output and tool events, actual model `gpt-6.1-sol`, matching original input identity and zero native recovery/replay attempts.
- Original run/conversation, prompt, configuration, synthesis context/instructions, eight complete candidate result records and answer hashes, eight candidate workspace inodes and the original **261,372,543-byte** event prefix unchanged. All three original failure status files are archived once.
- Seven other owner process identities and all **44** original native/helper processes serving the other eleven running Agents unchanged. Global pcrd **0.1.98/PID 48685** and loaded macOS **0.1.441/PID 4826** unchanged; no global service or client restart, signing or Keychain action.
- Original receipt-store identity, settings, drafts, question answers and the one original local queued message unchanged; no outbox or accepted handoff. Pairing, model availability and the existing authenticated relay connection remained healthy.
- A read-only observation of the actual main window showed all three synthesis tabs active with fresh output, eight completed candidates retained and Agent 2 still selected. No UI click, keyboard input, selection change or result adoption was used.

The post-recovery snapshot `snapshot-20261017T054109.658773Z.json` reports three active groups, fourteen running Agents and eight live owners. macOS 442 remains published but not installed: quitting the currently selected successful candidate still has automatic-adoption side effects, so this recovery does not bypass that independent installation gate. Research completion is not claimed.

Private evidence is in `CompetitionWatch/`: `legacy-synthesis-validation-20261017T053709.476572Z.json`, `legacy-synthesis-finehand-20261017-plan.json` and its attempt/dispatch records, and `legacy-synthesis-verified-20261017T054240.955123Z.json`. No new product release was needed; the deployed correction was the existing verified 0.1.98 executor replacing only this idle legacy owner.
