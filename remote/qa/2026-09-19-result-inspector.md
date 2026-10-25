# Result Inspector and Live Usage Acceptance

Date: 2026-09-19

## Scope and Root Causes

- The result inspector was a temporary view flag cleared when the workspace, turn, or prompt changed. It is now an explicit per-window preference owned by AppModel, with empty and preparing states instead of disappearing. SceneStorage alone failed the real relaunch check because this app deliberately disables AppKit state restoration; UserDefaults now persists the preference.
- Live worker events already contained cumulative usage breakdowns, but run summaries exposed only the final result's breakdown. The existing incremental event projection now also reconstructs per-agent usage. New snapshots replace old snapshots, retries reset their own counters, and finalized results remain authoritative. No protocol or persisted run-state version change was required.
- The file list only opened a general review, limited visible files, and could stop before the selected file's complete patch. Every listed path now opens its own review, including paths with spaces; pagination continues through that file rather than silently selecting the first file. Saved patches remain viewable after candidate directory cleanup.
- Interrupted-save recovery previously asked users to interpret internal sync-state labels. The choices now distinguish continuing with a result already present in the original folder from returning to result selection after an incomplete save. The explanation explicitly warns that returning does not undo files already written.

## Automated Verification

- Backend regression: 263 tests and 28 subtests passed, covering progress projection, workspace synchronization, plugin runtime, remote controller, conversation protocol, finalization, and work operations.
- Installed wheel subset: 20 tests and 2 subtests passed. Release bundle checks: 11 tests passed.
- macOS final source: 122 tests in 18 suites passed, plus protocol and desktop-state contract checks.
- Added coverage for cumulative usage replacement, duplicate events, retries, event replay, malformed counters, final-result precedence, per-window inspector persistence, and fetching every page of a specifically requested file via the client transport.
- Signed release build succeeded with the macOS 26.5 SDK. Strict signature verification passed for the installed application.

## Real Client Acceptance

Used an isolated native macOS bundle with the release binary, a separate preferences home and pcrd state directory, and a disposable workspace. No synthetic usage counter was substituted for model usage.

- Run `pcr-20260919T030907Z-b565981e`: sent a real prompt in the UI, produced three files, observed 62 reasoning tokens during execution and 93 at completion. Opened `second file.txt` and `last.md` directly and verified their individual additions. Adopted the result and verified saved patch access after candidate cleanup.
- Continuation `pcr-20260919T031212Z-4ec48796`: the inspector stayed open through submission and the next turn. Live usage increased from 452 tokens / 2 records to 673 tokens / 3 records before completion. Collapsing the breakdown persisted across turn changes and relaunch. The automation input method dropped Chinese characters in this continuation prompt, so this is state/usage acceptance, not validation of the originally intended text-generation task.
- New conversation: the inspector remained visible with an empty result state; returning to history restored the selected result. File-review dismissal restored the inspector.
- Relaunch initially exposed the SceneStorage persistence defect. After replacing it with the per-window preference, a fresh release-binary launch retained both the open inspector and collapsed breakdown.
- Run `pcr-20260919T033115Z-21911914`: restarted only the isolated daemon during a real Agent's 120-second sleep. Worker PID 68764 survived unchanged; the reconnected daemon tracked completion and returned `DAEMON-RESTART-VERIFIED`, 77 tokens / 3 records. Finalized only this isolated QA run afterward.
- Viewed the signed release in dark mode and enlarged text. The native usage disclosure, counts, empty file state, and side panel were legible without overlap in the inspected layouts.

## Publication and Activation

- First activated the previously staged macOS 0.1.369 and pcrd 0.1.47 fixes, after confirming no active Agent runs. Notified the user before proceeding with these inspector changes.
- Published macOS **0.1.370 (370)** to the authenticated Relay update channel. Release ID: `macos-release-09e1f341af8440a998328e3c14f6590e`.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.370.zip`, 8,500,491 bytes, SHA-256 `90a9dbef2bb5e6bfeea6750cc057bc882e6ec0e2fc8a3b4bfa413d580ff95cda`.
- Verified the real update check from build 369 and a private device-ticket download, including exact size and SHA-256.
- pcrd **0.1.48** wheel: `release/pcrd/parallel_codex_runner-0.1.48-py3-none-any.whl`, SHA-256 `2478419f2aa79ff24a1655eea612a511cab499c965b8a7634714d77f918dc929`.
- Installed the wheel and restarted the production daemon only after rechecking `active_runs=[]` and `active_operation=null`. Live `system.capabilities` reports 0.1.48; Relay reconnected.
- Backed up macOS 0.1.369, installed 0.1.370 at `/Applications/Parallel Codex.app`, verified its signature, and launched it. The editor was empty before replacement. Used exact-PID termination rather than ordinary Quit to avoid implicitly choosing a user's completed candidate during an application update.
- The existing server queue continued automatically as `pcr-20260919T033400Z-fd40a814`. Verified the original conversation, running five candidates, persistent result inspector, collapsible usage section, and usable composer in the installed client. No user Agent was stopped or user result manually accepted by this maintenance task.
- Android and Relay application versions are unchanged; their release-manifest entries were retained. This is not an Android deployment or remote-host-wide upgrade claim.
- Temporary upload removed. Isolated QA workers were finalized, and the preview client and isolated daemon were stopped. User web services were not stopped.

## Remaining Limits

- Usage updates when Codex reports real counters; it is not a fabricated per-second estimate. Exact report cadence depends on the model and execution transport.
- The interrupted-save dialog copy was inspected in source, not forced on a real user run. No production failure state was fabricated for presentation testing.
- During rapid appearance and text-size changes in the isolated settings window, the UI automation surface timed out. A process sample was mostly idle, and the log included existing collection-layout width warnings. Relaunch restored normal interaction; this does not establish that all dynamic appearance/layout transitions are fixed.
- No new Android UI acceptance or prolonged performance benchmark is claimed for this macOS inspector iteration.

Evidence logs, signed builds, and previous application backups are under `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/Inspector-20260919-Rl4Gva`.
