# Sync Recovery and macOS Native Title Acceptance

Date: 2026-09-19

## Incident Recovery

- Run: `pcr-20260918T170716Z-270b3247`, selected candidate: AGENT-007.
- Candidate: `/Users/mingliangxu/.codex_parallel_runs/20260919_010721/workspaces/agent_007`.
- Original: `/Users/mingliangxu/Desktop/siclab-next`.
- The previous worker discarded rsync stderr. Its exit code alone cannot establish a permissions failure or identify the failing path.
- Both trees contained running applications and SQLite WAL data. The old full-tree mirror included ignored runtime data, lacked captured diagnostics, and could leave finalization in `sync_ambiguous`.
- Preserved the original tree with `ditto` and made an online SQLite backup before recovery. Backups remain in `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/SyncRecovery-20260919-Y5BIKt` (`siclab-next-before` and `source-business.sqlite`).
- Both Git HEADs were `d32b02b989380e38015e60125b8d67e8d5ce0253`; the candidate had no staged changes.
- A checksum dry run identified eight remaining source files: `src/grouping.css`, `src/grouping.tsx`, `src/styles.css`, `src/pages/archives.tsx`, `src/pages/data.tsx`, `src/pages/experiments.tsx`, `src/pages/schedule.css`, and `src/pages/schedule.tsx`.
- Applied only the source-tree sync while excluding `.git`, runner metadata, `.local`, `node_modules`, `.playwright-cli`, and `dist`. A repeated checksum dry run was empty.
- Used the live `runs.recover_finalization` and `runs.accept_agent` RPCs, not manual state-file edits. The run became `finalized`, `synced_back=true`, with no error and successful session promotion back to the original directory.
- Kept candidate directories because they still contain running services. No web server or Agent was terminated. The original web service on port 8802 returned HTTP 200.
- The user subsequently continued with `pcr-20260919T013843Z-f630e5b6`, whose `continued_from_run_id` points to the recovered run. That turn later reached `completed`; it was not accepted or finalized by this maintenance task.

## Changes

### Shared Sync and Cleanup

- Preserve Git-ignored runtime paths from both trees, while allowing tracked deliverables to override ignore rules.
- Capture rsync diagnostics; use checksum-based copying without owner/device replication or directory timestamp churn.
- Fall back to same-directory temporary files and atomic replacement. Do not truncate files held open by a running reader.
- Retry transient failures up to three times and compare source-before, source-after, and destination manifests before reporting success.
- Fail explicitly on inaccessible directories, unsupported special files, or a source replacement that would destroy protected runtime descendants.
- Preserve both rsync and fallback diagnostics when recovery fails. Do not convert an uncertain copy into a successful finalization.
- Defer candidate cleanup while a process has its working directory inside the candidate. Do not kill that process or make successful sync depend on cleanup.
- Runtime-data protection depends on Git ignore rules for Git workspaces. This is not a live database merge facility for arbitrary non-Git projects.

### macOS

- Give the root view sole ownership of the native window title and subtitle. Remove competing child navigation titles, the custom principal title, and asynchronous manual title overrides.
- Restore native title visibility without changing window geometry or toolbar style at runtime.
- Keep transcript maximum width at 2000 points; independently cap the centered composer at 1040 points.

## Verification

- Backend regression suite: 415 tests and 11 subtests passed. This preceded the final diagnostic-message wrapping adjustment.
- Final packaged-wheel regression subset: 50 tests and 7 subtests passed, including the added dual-diagnostic failure test.
- macOS: 119 tests in 18 suites passed. Includes real hosted composer layouts at 2400, 1440, and 540 point widths, plus native-title visibility checks.
- Signed macOS release build completed with the macOS 26.5 SDK; signature and designated requirement verified.
- An isolated preview bundle was exercised in dark loading/smoke state and then against an existing loaded conversation in light mode. Sidebar toggling, native title/status display, window zoom, and the narrower centered composer were visually verified.
- Continuous mouse-drag resizing was not verified: the automation surface could not perform that drag. Do not treat the zoom check as a substitute.
- No messages were sent or results finalized during UI acceptance. The preview was terminated by its exact PID; the production client and pcrd were not restarted.
- `git diff --check` passed.

## Release and Activation

- macOS `0.1.369` (build 369) published to the authenticated Relay update channel.
- Release ID: `macos-release-187091ed520e4f56bccc94ecaea3a6fc`.
- Archive SHA-256: `edc446990a305de605a8c7b8bf5d7b1f0d2db46bed73a097e9881633ab9441f5`.
- Verified the actual update check from build 367, downloaded using a device-bound ticket, and rechecked archive version and signature.
- pcrd `0.1.47` is built and tested at `release/pcrd/parallel_codex_runner-0.1.47-py3-none-any.whl`; it is not installed in the running service.
- Wheel SHA-256: `ea0079fec658129cfb8aa3b65e2d62e33037d8c6a12edaa9f892e9b023c535b1`.
- Updated the local unified release manifest for macOS and pcrd only; retained Android and Relay records.
- Per the user's instruction to publish without restarting, the installed client remains `0.1.367` and installed pcrd remains `0.1.46`. The incident recovery is already effective; the new general sync and UI fixes require later activation.

## Subsequent Activation

The user subsequently authorized deployment. macOS 0.1.369 and pcrd 0.1.47 were installed and started on 2026-09-19, then superseded by macOS 0.1.370 and pcrd 0.1.48 with the result-inspector improvements. See `2026-09-19-result-inspector.md` for live deployment and queue-continuation evidence. The deferred activation statements above describe the earlier publication-only stage.
