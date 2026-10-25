# Live Workspace Snapshot Repair

## Cause

- The failed source-mode run was `pcr-20260919T172959Z-0c7c150b`.
- No Agent had started. Preparing the review baseline failed inside `shutil.copytree`.
- The source project's `work/wan22_bulk_1000/local_pipeline.py` creates per-job
  directories under `outputs/.../.work`, then deletes them in `finally`.
- Snapshot enumeration raced that normal cleanup. This was not a permissions
  failure or a reason to stop the source application.

## Change

- Plain folders and isolated Git clones now use the same snapshot copier.
- A vanished child is omitted only when the failure names that source path and
  a fresh `lstat` confirms it is gone. Partial destination copies are removed.
- Existing `.work` files, broken symlinks, file modes, Git index isolation,
  submodules, and the established exclusion rules remain supported.
- Permission, storage, destination, replaced-source, and missing-root errors
  still fail. Actual sync-back remains strict and is not affected.
- No layout, source-project, pipeline, or conversation-state files were edited.

## Verification

- Before the fix: all six targeted directory/file disappearance cases failed,
  including the same aggregated `shutil.Error` as the reported incident.
- Complete focused repository suites: 108 passed, 7 subtests passed.
- Complete plugin-runtime suite: 101 passed, 2 subtests passed.
- Built wheel installed into an isolated target: 37 tests passed, including
  source-mode Agent startup with a disappearing baseline directory.
- A broader all-core run was interrupted after 99 passed because unrelated
  tests were repeatedly launching live Codex config readers. It is not counted
  as a completed full-suite run. Direct zip-import is not a supported runtime;
  wheel acceptance used a normal unpacked installation.
- Copied the real source folder to a disposable maintenance directory:
  8,458 files, 2.88 seconds. The pipeline source SHA-256 was unchanged before,
  in the snapshot, and after copying. The disposable copy was removed.

## Deployment

- Built and installed pcrd `0.1.55` using the existing release-bundle process.
- Wheel SHA-256: `e73438d35f1d3388a38793475610cd1ed0e4c3b51b2456db51dde52fbde187c3`.
- Old package backup:
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SnapshotCopy-20260920-cd2TY0/pcrd-0.1.54-before.tgz`.
- Scheduler `74470` exited gracefully; replacement scheduler is `92389`.
- All six worker PIDs survived: `25298`, `40803`, `64588`, `66223`, `83907`, `91409`.
- Health is `ok`, Relay is `connected`, and startup warnings are empty.
- Existing web service on port 9001 returned HTTP 200 before and after restart.
- macOS remains `0.1.379`; no client, Agent, or source web service was restarted.

## User Conversation

- During the repair, the user had already discarded the failed run and resent
  the same question. No duplicate prompt was submitted by this repair.
- Replacement `pcr-20260919T174419Z-3aac95b2` succeeded and was finalized.
- The following turn `pcr-20260919T174830Z-8a3aaab6` also completed successfully.
- Read-only inspection of the formal macOS client confirmed both replies are
  visible in the original conversation, with no preparation error displayed.
