# Synthesis History Schema Hotfix

## Scope And Authorization

- The user authorized a hot update and continuation of recently failed synthesis Agents, without interrupting existing work.
- No macOS build, signing, keychain operation, client restart, global service restart, adoption, candidate replay, or research-directory cleanup was performed.
- This is a targeted executor handoff, not a global pcrd upgrade.

## Confirmed Cause

The latest two completed batches each had eight successful candidates and three synthesis preparation errors:

```text
synthesis preparation failed: Unsupported Codex history schema;
no global database was copied: guardian_review_feedback, thread_read_receipts
```

The failed synthesis slots had no native command, execution receipt, thread, workspace, or logs. Each retained only `status.json` and an empty private home. No original native synthesis submission needed to be replayed.

The shared Codex state database had migrated to state schema 61 / projection schema 8. Codex CLI 0.159.2 creates state 58 / projection 7 in an empty home, but successfully resumes the reviewed 61/8 private histories. Removing only migrations 60/61 and their new tables/indexes exactly reconstructs the previously supported state-59 schema/migration fingerprint.

## Fix

- Retain the exact state-59 contract and add reviewed state-60 and state-61 fingerprints.
- Export `guardian_review_feedback` and `thread_read_receipts` only for selected thread identities. Keep the guardian feedback foreign-key dependency on the selected thread.
- Preserve read-transaction consistency, compact row exports, storage budgeting, atomic replacement, and unknown-schema rejection. Never copy the whole history library as a fallback.
- Recover only unchanged, unsubmitted synthesis preparation failures under the existing per-run operation lock, original-input projection, private-artifact fingerprint, and durable idle-owner checkpoint.
- Archive original failure evidence; preserve original run, conversation, prompt, candidate results, and physical synthesis IDs 9/10/11.
- Wait for the old owner to exit and for the existing daemon to reap its cached process before publishing a replacement. A publication gate prevents all task events, private preparation, and native submissions before the new PID/operation are durable.
- Give the PID commit a timestamp newer than the visible batch event, preventing a delayed old reader from overwriting the new owner with a same-timestamp stopped record.
- Stop intent, newer tasks, changed inputs, existing native artifacts, queued operations, uncertain submissions, and duplicate recovery requests remain protected.

The owner-writeback validation covers the existing same-host merge rules and the observed handoff window. It is not a claim of general generation fencing across arbitrary clock rollback or writers that bypass the existing state store. PID reuse causes the retirement observer to refuse progress, rather than skipping its checks.

An unpublished 0.1.96 candidate was rejected before any production recovery after review identified the same-timestamp publication race. Its wheel/runtime were not overwritten or enabled for user work. The final verified executor is 0.1.97.

## Validation

- Final source: 1,197 tests and 289 subtests passed.
- Installed 0.1.97 runtime: 1,196 tests and 289 subtests passed; only the repository-layout check was excluded from the installed run.
- Source and installed native acceptance each exercised three real Codex history resumes and three synthesis executions after replacement of a disposable 0.1.92 idle owner.
- Native acceptance used loopback-only model fixtures, submitted no real user turns, retained original history, excluded unrelated rows, preserved eight synthetic candidate results, and left a neighboring background process intact.
- Focused recovery tests include changed/uncertain receipts, late stops, changed context, duplicate dispatch, old-process reaping, delayed old-reader writeback, and the existing unpublished-worker/publisher-exit gates.

## Release And Actual Recovery

Backend 0.1.97 was published to `release/pcrd/parallel_codex_runner-0.1.97-py3-none-any.whl` and the release manifest. The existing macOS archive and catalog were not changed.

Wheel SHA-256:

```text
4b1eebcf49e816f80e08e35755fa21a4dc81510ae09e6afa2fef0d6122e4260d
```

Actual targeted handoffs:

| Original Run | Synthesis IDs | Old Idle Owner | New 0.1.97 Owner |
| --- | --- | --- | --- |
| pcr-20261016T045048Z-0fde9458 | 9, 10, 11 | 85300 | 5472 |
| pcr-20261016T052608Z-8db73f6e | 9, 10, 11 | 3349 | 6502 |

At 2026-10-16 21:57 Asia/Shanghai, all six had accepted native turns and new model/tool items, not merely queued status. The existing daemon also reported all six as running or successful, without the preparation errors. Their original parent-session identities, prompts, contexts, candidate result records, sixteen candidate workspace inodes, candidate answers, and original event-file byte prefixes were verified intact. Original failure status files were retained in retry history.

The first dispatch succeeded but an observation check incorrectly expected the venv launcher path in macOS `ps`; macOS displayed the underlying framework Python executable. Verification was corrected to check the startup package root, Python path, and venv launcher provenance. The saved original dispatch receipt was reconciled, never resubmitted. Each batch had exactly one recovery dispatch.

Global pcrd remains healthy at 0.1.92, PID 73487. The client remains 0.1.436/build 436, PID 4569, with its executable inode, bytes, pairing, receipt-store identity, and LaunchAgent unchanged. The previously running batch `pcr-20261016T044140Z-9d65cdfe`, owner 77777, retained its original execution identities and workspace inodes. No running user Agent or computation was stopped or restarted by this work.

This confirms recovery of these six synthesis executions, not completion of their research work. The schema fix is active in their two 0.1.97 executors; the global service and other pinned workers have not been upgraded.

Private detailed evidence, immutable artifact checks, test reports, dispatch receipts, and post-handoff verification are retained under:

```text
/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SynthesisSchema-20261016/
```
