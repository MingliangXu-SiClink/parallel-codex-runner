# Five-competition retained execution recovery

Verified on 2026-10-30 at 03:21 Asia/Shanghai. Source checkout:
`/Users/mingliangxu/Desktop/parallel-codex-runner`.

## Outcome

All 14 unfinished, already-created branches below have new native tool
execution after recovery. These are not merely `running` records or new PIDs.
Twenty successful branches were not rerun; their recorded result, command,
instructions and receipt files retained their pre-dispatch hashes.

| Competition | Original run | Restored branches | Successful branches preserved |
| --- | --- | --- | --- |
| EEG compression | `pcr-20261029T160020Z-41c2865d` | Candidates 1-5 | None in this run |
| Fine-hand decoding | `pcr-20261029T071015Z-fe50f18b` | Candidates 1, 5 | Candidates 2-4 |
| Cross-day behavior | `pcr-20261029T114318Z-11278dd5` | Synthesis 3, slot 8 | Slots 1-7 |
| Memory decoding | `pcr-20261029T005712Z-1b3fe033` | Synthesis 1-3, slots 6-8 | Candidates 1-5 |
| Epidural decoding | `pcr-20261029T005826Z-831ccccc` | Synthesis 1-3, slots 6-8 | Candidates 1-5 |

The compression and fine-hand runs still have their normal synthesis stages
configured. Those not-yet-created branches are left to the existing worker's
candidate-completion flow, not launched separately.

## Root causes and changes

1. The active daemon was 0.1.131; the partially staged 0.1.132 was not active.
   External shutdown wrote both `stop` and `shutdown` signals with the retiring
   worker PID. The unfinished recovery path required the stop signal to be
   absent and the old worker to remain alive, excluding this actual shutdown.
   Recovery now requires the durable dead-owner shutdown checkpoint, matching
   signals, retained execution fingerprints and absence of explicit user stops.
   It consumes the old terminal events before publishing a replacement owner.
2. Memory and epidural synthesis had a different failure: private
   `config.toml` parsing failed with `duplicate key` before native input was
   accepted. These six branches require first-submission recovery, not a
   continuation of an accepted turn. A separate restricted RPC proves the
   configuration-stage failure and empty submission identities before allowing
   the original prepared synthesis command and instructions to run once.
   The old malformed private config had already been cleaned up; its original
   writer is not established. The current source config was validated before
   use. This is not a claim that a config-generation bug was identified/fixed.
3. Retained execution dispatch now supports the original synthesis role and
   reports actual successful results rather than counting every selected slot
   as successful regardless of outcome.

Changed implementation: `app.py`, `plugin_runtime.py`, `plugin/worker.py`,
`remote/controller.py`; regression coverage in
`tests/test_in_place_agent_recovery.py`. Prior unrelated source edits were kept.

## Recovery semantics and verification

- Eight previously accepted branches retained the same run, Agent, execution
  workspace, private Codex home, native thread and original instructions. Native
  reconciliation resumed the thread and accepted one short recovery input per
  branch in a new turn. The original task was not submitted again.
- Six configuration-failed branches used their original prepared synthesis
  input for its first accepted submission, in the retained home/thread. Their
  candidate results and synthesis context were not rebuilt.
- The old command, receipt, logs and terminal metadata were retained under each
  branch's existing `retry_history`. No credentials were archived there.
- Native user-input records and new command/tool events were checked, not only
  turn counts. All 14 had completed tool events; fine-hand 1 and 5 had no new
  assistant prose yet at the last check but did have continuing tool execution.
- Existing adapter behavior persists `submitting` and its client message ID
  before `turn/start`; ambiguous replies reconcile native input identity instead
  of replaying the prompt. Completed native turns are collected, active turns
  attached, and permanent errors remain blocked.
- This operation did not stop remote computations, finalize results, change
  accounts, alter concurrency or send the user's unsent client draft. The
  read-only remote snapshot found the same six GPU process IDs as the earlier
  snapshot, but did not establish per-Agent ownership of every remote job.
  Therefore absence of duplicate remote computation is not claimed from those
  process counts alone.
- This was an explicitly authorized recovery, not automatic boot recovery or
  a new scheduler. All five current runs were checked before dispatch; prior
  user-stopped and finalized runs were excluded.

## Tests and deployment

- Source suite: `pytest -q tests`, 1,345 tests and 367 subtests passed.
- Installed 0.1.133 runtime: 47 focused recovery/runtime tests passed, including
  the existing 0.1.131 reader compatibility probe.
- Focused tests exercise actual worker subprocesses with an isolated fake native
  peer. Tests cover retained candidate/synthesis dispatch, no original input
  replay, configuration pre-input evidence, actual failure counting, user-stop
  refusal, live-owner refusal and changed-identity refusal. They do not prove
  every crash window or remote research side effect is covered.
- pcrd **0.1.133** installed and activated locally through the existing
  LaunchAgent, after confirming there were no active workers. The old daemon
  exited normally. All run bindings and the client's stored state were retained.
- Local release wheel and `release/manifest.json` updated. SHA-256:
  `03e2f95985bc0135c610bb5e704a2604948ff9cf4b8a256e252351d79c49c854`.
- macOS **0.1.496** reopened without rebuilding. The actual selected cross-day
  conversation showed new output and an active synthesis branch; the user's
  draft was left intact. Relay was connected. No cloud release catalog was
  published, and no Relay/client release was claimed.
- The 1-second navigation / 10-second response performance acceptance gates
  were not measured in this recovery operation.

## Evidence / handoff

Private evidence directory:
`~/Library/Caches/ParallelCodexMaintenance/RebootRecovery-20261030/`.

- `before.json`, `*-dispatch-intent.json`: exact original targets and file hashes.
- `build.json`, `activation.json`, `publication.json`: package/runtime evidence.
- `*-dispatch-reply.json`: single authorized dispatch per original run.
- `progress-20261029T191527Z.json`, `progress-20261029T191731Z.json`,
  `progress-20261029T192143Z.json`: increasing native tool activity, input
  identities and preserved successful-branch hashes.
- Read-only `verify_progress.py` can be rerun. Do not rerun `restore_targets.py`
  or activation/publication scripts as a health check; they mutate state.

No running branch needs another restart or retry on the basis of this report.
