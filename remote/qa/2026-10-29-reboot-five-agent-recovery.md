# Five-Agent Reboot Recovery

## Scope and Cause

- User requested recovery of the five original Agents after a Mac reboot.
- Verified boot time: 2026-10-29 08:04:33 Asia/Shanghai.
- Affected workspace: `运动行为跨天解码`.
- Original run: `pcr-20261028T164042Z-68b8df05`.
- Original root: `/Users/mingliangxu/.codex_parallel_runs/20261029_004043`.
- Agents 1-5 had accepted native receipts, retained private histories and workspaces,
  dead pre-reboot PIDs, and no user-stop signals. The supervisor was recorded as
  interrupted/crashed. No replacement conversation or initial task was created.
- Recovery with installed 129 exposed a separate compatibility bug: the selected
  valid Codex token file omitted optional `auth_mode`. Native Codex accepted it,
  but PCR rejected it and refused to reuse the previous account. That attempt
  submitted no native input; all five original receipts and histories survived.

## Fix and Validation

- `codex_auth.file_auth` now accepts missing/null `auth_mode` only when the existing
  complete ChatGPT token/account/user validation passes. Explicit alternative auth,
  conflicting API keys, incomplete tokens and mismatched accounts remain rejected.
- The selected login file was not rewritten, refreshed manually or replaced.
- Added legacy-format recovery, unchanged-history/source-file and negative auth tests.
- Targeted source suite: 62 tests plus 63 subtests. Installed wheel: 62 tests.
- Full frozen-source suite: 1,336 tests plus 358 subtests passed.
- First full run inherited the private evidence directory's restrictive umask and
  failed one existing file-mode fixture (100600 versus 100644). No product/test
  behavior was changed for it; the complete rerun under standard umask 022 passed.
- Wheel 0.1.130 SHA-256:
  `fe044aedd3937ba6c6308bf1237f04f188822f5b7761ad00a1c29e82cb03e86f`.
- All 324 Python files compared against 129; only `codex_auth.py` changed.
  All 323 installed core source files matched the new wheel.

## Deployment and Recovery

- Updated the local release wheel/manifest and source plugin version to 0.1.130.
  This does not claim a remote backend catalog or macOS release.
- Staged immutable runtime 130 with the previous dependency versions.
- Global daemon activation was cancelled before any stop: a new, healthy compression
  run had started concurrently. Daemon remains 129; macOS and Relay were not restarted.
- Replaced only the durably idle failed recovery owner using the existing locked,
  fingerprint-checked `resume_agents_in_place` API and installed runtime 130.
- New target supervisor PID: 17007; operation:
  `pcr-20261028T164042Z-68b8df05-01793235011564823000-6e0a574897`.
- Preserved the other active compression owner PID 6767 and its start identity.
- Original run, conversation, command, input/config hashes, private thread/home and
  physical workspace identities matched. New control turns are marked `pcr_recovery`;
  the accepted original user input was not replayed.
- Directed remote checks before recovery found no live processes owned by the five
  known project directories. Agent 4's incomplete result remained on disk; after
  native reconciliation it resumed that comparison with `--resume`, preserving the
  existing three result entries. Maintenance did not start an experiment itself.

## Evidence

Private records (no tokens):
`~/Library/Caches/ParallelCodexMaintenance/RebootRecovery-20261029/`.
Includes baseline native offsets/receipts, both recovery operation receipts,
remote checks, per-Agent incremental verification and build/test hashes.

At 08:53, Agents 1, 2, 4, 5 had new native tool calls/results. Agent 3 was in native
`contextCompaction`, not a proven failure; no repeated recovery was attempted.
The old auth error retained in the receipt is historical, not a fresh failure.

## Final Verification

At 08:56:26 Asia/Shanghai, all five original Agents had new native tool calls and
results after recovery. Agent 3 completed compaction and continued execution.
These are appended private-native history events after the saved baseline offsets,
not repeated projections, cache refreshes or acceptance-only receipts.

| Agent | New native events | Latest native event (local time) | Original identity |
| --- | ---: | --- | --- |
| 1 | 65 | 08:56:18 | Matched |
| 2 | 70 | 08:56:19 | Matched |
| 3 | 77 | 08:56:12 | Matched |
| 4 | 78 | 08:54:59 | Matched |
| 5 | 96 | 08:55:59 | Matched |

Agent 4 was in an explicit native sleep while its resumed remote comparison
continued: PID 1905599, unchanged start tick 55172286, CPU ticks increased from
4473 to 32830. The three previously completed result entries remained preserved.
Individual tool errors followed by further native execution were not treated as
recovery failures or reasons to restart an Agent.

At 08:57, the protected compression owner was still PID 6767 with the same start
identity. Its candidates had completed and its synthesis Agents were running,
showing that the unrelated run continued advancing during this recovery.

At 08:58:47, Agents 1-4 had continued adding native events. Agent 5 had returned
its final response (2,531 characters) and a native `task_complete` at 08:57:45;
its PCR status was `success`, not another interruption. This is completion of its
recovered turn, not a claim that the competition's research objective was achieved.

Final evidence: `verification-5.json`, `verification-6.json`, `remote-final130.json`, and
`protected-owners-before130.json` in the private evidence directory above.
Only the generated frozen build source and duplicate private wheel were removed;
the release wheel, installed runtimes, original research files and audit evidence
were retained. No client restart, finalize/adoption or global daemon switch occurred.
