# Workspace Links Independent Of Run State

## Cause

- `workspace.file.read` always expected an isolated `workspaces/agent_NNN`
  directory, even for source-workspace execution. Valid source paths were
  rejected as belonging to another run.
- macOS sent every filesystem link through the file-download endpoint,
  including folders. It also attached the currently displayed run/Agent and
  discarded downloads or closed previews when the user changed Agent.

## Changes

- Added authenticated, read-only `workspace.item.resolve` to local IPC and the
  encrypted Relay allowlist. It distinguishes files from directories, returns
  the verified path and source/candidate scope, and pages remote folders at
  200 entries. Workspace ownership and symlink/escape checks remain enforced.
- Absolute source-workspace references resolve without loading a run or Agent.
  Relative candidate links use the originating transcript/review context.
  Source-mode runs resolve to their actual source directory, including through
  the existing file-read API used by Android.
- New clients request literal paths when transferring files. A removed
  candidate is not silently replaced with a same-named source file. Existing
  file-read clients retain their previous transfer contract.
- Local folders open in Finder; local files preview directly without being
  copied through the transfer cache. Remote paths are never treated as paths
  on this Mac. Remote folders have a paged in-app list.
- Preserve open documents and their originating Agent during candidate
  switching. The preview header identifies candidate versus workspace files.
  HTTP/HTTPS links continue opening independently in the default browser.

## Verification

- Python source and isolated installed-wheel suites each passed 263 tests and
  38 subtests. Includes IPC/Relay permissions, path traversal, symlinks, source
  mode, removed/mismatched candidates, paged files/folders, and persistent
  workers surviving scheduler shutdown.
- Final Swift/AppKit suites: 178 tests in 25 suites passed. New checks cover
  source provenance through continuation, cross-Agent preview retention,
  asynchronous file opening with a different visible Agent, and refusal to
  treat remote paths as local files.
- Built and signed the actual macOS app. Native isolated-client walkthrough:
  opened source Markdown and candidate Markdown while Agents were running;
  opened a folder in Finder and a website in the default browser. The first
  walkthrough found preview dismissal on Agent switching; this was fixed,
  rebuilt, and rechecked with Agent 2's file remaining visible while viewing
  Agent 1. No real model requests were created by the fixture.
- In the installed production client, used conversation search to locate the
  user's original `videos_60fps` link and clicked it. Finder opened the exact
  reported directory under `outputs/WAN22-motion-v3-pilot8-20260922`, showing
  its eight videos. No working-copy mismatch or file-type error appeared.
- The live resolver also opened that source folder with both its completed
  run ID and a nonexistent run ID. This verifies independence from run lookup,
  not a bypass of registered-workspace access controls.
- Remote folder paging/host isolation is covered by protocol and client tests;
  a real remote-host folder UI walkthrough was not performed in this release.

## Deployment

- Published signed macOS **0.1.382 (382)** through the existing private Relay
  update channel; installed it in `/Applications/Parallel Codex.app` and
  restarted the client after user approval. Installed binary matches the
  verified build and passes strict code-signing validation.
- Installed **pcrd 0.1.58** and gracefully restarted only the local scheduler.
  PID 45254 exited with code 0; replacement PID 71710 reports healthy, Relay
  connected, no startup warnings, and the new resource-resolution capability.
- Existing worker PIDs 45755 and 67561 survived with unchanged start times;
  the active run remained `pcr-20260922T033950Z-71d855e7`, and its stdout kept
  growing after deployment. Web watcher PID 21279 survived; port 8811 returned
  HTTP 200. No task was adopted, discarded, restarted, or replayed.
- Android 0.1.266 and Relay 0.1.18 artifacts were unchanged. Other machines'
  pcrd services were not restarted; the new resolver requires pcrd 0.1.58 there.

Release ID: `macos-release-df9bf47eb1af49d9b03ce988de99a712`

Archive SHA-256: `28197be2cb0d38d498c5ed38504e33ca49625f9d4f3361d353ad0ca17cc1a1a6`

Wheel SHA-256: `f9b7e90e3ec40f3c92f578ff6f8d445bc1cd97bc1ceb6e862bd2e001e852e4e1`

Build logs, isolated fixture, previous installed app and rollback wheel:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/WorkspaceLinks-20260922-CfkPyn/`
