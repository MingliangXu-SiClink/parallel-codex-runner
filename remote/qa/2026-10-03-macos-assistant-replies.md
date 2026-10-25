# macOS Intermediate Replies Outside Work Disclosures

## Scope

- User requested the same reading sequence as Android: work disclosure, visible intermediate reply, next work disclosure, next reply.
- macOS-only change; no pcrd, Android, run configuration, or research workspace changes.
- Target macOS version: 0.1.397, build 397.

## Cause And Repair

- `TranscriptFormatter.isAgentWorkBlock` previously classified assistant commentary output as internal work, so the reply disappeared when the work disclosure was collapsed.
- Commentary now stays in the main transcript and separates neighboring work sections. Thoughts, commands, and tool activities remain independently collapsible.
- Intermediate replies use normal answer typography and the existing application bitmap icon instead of the smaller gray work style.
- Layout metric cache schema is advanced from 13 to 14 so cached older commentary heights cannot clip the larger reply text.
- Chronological ordering, stable block identities, final-answer geometry, and the bounded borderless work scroll view are retained.

## Verification

- Complete macOS test script: 264 tests in 35 suites passed; protocol and desktop-state contracts passed.
- Added regression coverage for raw, typed, and restored commentary, repeated updates, final-answer arrival, independent disclosure expansion, and mounted reply views during layout publication.
- Signed release build succeeded with the configured macOS 26.5 SDK; icon and bundle checks passed.
- Independently launched the tested release in two isolated QA clients with private application homes and read-only synthetic IPC controllers. No real Agent was started or mutated.
- Native accessibility and screenshots show all three intermediate replies outside separate work disclosures. Expanded work remains bounded and internally scrollable; collapsing it leaves the reply visible.
- A streaming fixture showed the first reply while later commands continued, then preserved all three replies and the final answer after completion.
- Checked light/standard and dark/extra-large text: paragraphs wrap without overlapping neighboring content.
- The workerless fixture intentionally has no persistent worker, so the existing unavailable-worker recovery controls were not used as run-state acceptance evidence.
- Both QA clients and fixture processes were closed after inspection.
- Embedded ChatGPT Web consultation was attempted but not submitted because the page displayed a temporary functionality restriction. Local code, regression tests, and native client checks were completed independently.

## Release State

- Published macOS 0.1.397/build 397 to the private relay catalog. The catalog's version, signing identity, and archive SHA-256 match the verified bundle; no relay service restart was performed.
- Release archive SHA-256: `2996db87a9159fbacecb8fc8d4ae126ed8a37836df0afb312b74b4dcf984cb59`.
- Installed the signed bundle at `/Applications/Parallel Codex.app`, preserving a full 0.1.396 backup. Only the exact formal client process, PID 36787, was terminated for replacement.
- Restarted the formal application from its exact installed path. New PID 84346 started at 10:53:03 Asia/Shanghai on 2026-10-03. Its plist reports 0.1.397/build 397 and its binary SHA-256 matches the tested release.
- Installed binary SHA-256: `a2922f2548c9401dea64a0478caca0108bef78bfc11494da5914a5100ac3ba25`.
- Native post-restart inspection of the existing running conversation shows intermediate replies independently before and after work disclosures, with normal answer typography and the application icon. The existing conversation and result sidebar were restored.
- pcrd remains 0.1.66 with the same PID 20382 and unchanged LaunchAgent definition. `system.health.pcr.ok` is true; `active_operation` is null.
- Run `pcr-20261003T022623Z-6a750dd0` remains active before and after installation/restart. All 32 observed Python, Node, and Codex processes retained their PID/start-time identity during replacement; none exited. No Agent, pcrd, or web service restart was requested or performed.
- The non-macOS release manifest components and artifacts were preserved unchanged.

Evidence directory: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AssistantReplies-20261003`.
