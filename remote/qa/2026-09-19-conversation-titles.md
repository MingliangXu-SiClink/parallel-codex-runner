# Conversation titles: 2026-09-19

## Implemented behavior

- The visible conversation title sits at the upper left of the main chat pane,
  rather than above the sidebar. The native window title remains available to
  the Window menu and accessibility without being drawn a second time.
- Click the title, use the conversation context menu, or press Cmd-Shift-R to
  rename. Enter saves; Escape cancels. Empty and over-200-character names cannot
  be submitted. Long titles leave room for status and conversation actions.
- Manual names are stored by pcrd, scoped to machine/workspace/conversation,
  and have monotonically increasing revisions. Polling, cache restoration,
  subsequent turns, and imported-session conversion preserve a manual name.
- Renaming changes metadata, not the original prompt, active turn, candidate
  selection, or execution. The new action is advertised by capabilities and
  allowed through the encrypted Relay path. Older daemons remain readable;
  renaming requires pcrd 0.1.51.

## Codex capability verification

The installed `/opt/homebrew/bin/codex` reports `codex-cli 0.153.4`.
An isolated app-server was launched with a test `developer_instructions`
override, then initialized and queried using `config/read`. The resolved field
matched the supplied value exactly. No model request was sent and no permanent
Codex configuration was changed. This confirms the local CLI, not uninspected
remote hosts. Developer instructions supplement developer-level context; this
does not replace the model's system instructions.

## Automated verification

- Full backend suite: 707 tests and 36 subtests passed.
- Focused tests against the installed release wheel: 134 tests and 14 subtests.
- Final macOS suite: 144 tests in 21 suites, plus transport/static checks.
- Android protocol/unit regression: 263 tests, no failures, errors, or skips.
- Release/packaging checks: 14 tests passed.
- Signed macOS release build and wheel build succeeded; archive signature and
  release checksums verified. `git diff --check` passed.

New tests cover stale revisions, workspace isolation, canonical/imported
conversation identity, subsequent turns, original prompt preservation, older
protocol decoding, and title/status layout at multiple widths with the sidebar
both shown and hidden.

## Native acceptance

Used the real native client in a separate app bundle and isolated fixture home.
The fixture used the actual RemoteController, conversation store, and IPC server,
with synthetic run data and no model execution or production conversation edits.

1. Clicked the title with the pointer and opened rename using Cmd-Shift-R.
2. Saved a Chinese name with Enter; main header, sidebar, and semantic window
   title changed together. Server metadata was persisted; prompt was unchanged.
3. Verified the sidebar context menu, empty/overlong validation, and Escape.
4. Resized the window, tested long names and collapsed sidebar, and verified
   that status/actions and native window controls were not overlapped.
5. Quit/relaunched and opened a second conversation window; the saved name
   remained. Inspected the final title layout in both dark and light appearance.
6. Quit the isolated client and gracefully stopped only its fixture server.

The fixture does not implement model-options or paired Relay configuration;
background failures from those unrelated endpoints are not acceptance evidence
for those features. A separate pre-existing behavior was observed: changing
appearance in Settings did not immediately update an already-open secondary
window; relaunch applied it. That cross-window settings issue is not fixed here.

## Release and deployment status

- macOS 0.1.376 (376): signed archive published to the private Relay channel.
- pcrd 0.1.51: wheel built, installed, and gracefully restarted after approval.
- Android 0.1.266 (267) and Relay 0.1.18: no version changes this round.
- Authenticated update check and actual download verified build 376, size
  8700959, and SHA-256
  `f5ecec9791bbd88a631f3b5c621ee43fb11c1833f3fae5e96bbb81cf24913bb2`.
- Release ID: `macos-release-474ef0e36806458f8716ef10b027fc8f`.

After the user approved the client update only, macOS 0.1.376 was installed and
launched from `/Applications/Parallel Codex.app`. The previous signed 0.1.375
bundle is retained under `installed-before-376` in the evidence directory.
The installed executable matches the verified release build and its signature
passes strict verification. The real client restored the selected workspace,
conversation, running synthesis Agent, result inspector, and empty composer.
The title is visibly at the upper left of the chat pane without duplication.

pcrd deployment was initially deferred, then explicitly approved separately.
The verified 0.1.51 wheel was installed and only the old scheduler PID 33384
received SIGTERM. New scheduler PID 24294 reports 0.1.51, and Relay reconnected.
Both direct IPC and an authenticated encrypted Relay request advertise
`conversations.rename`. No production conversation was renamed for validation.

All four persistent workers (25298, 40803, 64588, 66223) survived. No Agent was
actively executing at this restart's preflight. The web services on ports 8891,
8920, and 8950 returned HTTP 200 before and after. The client process state was
left unchanged; no client restart was performed with this scheduler update.

## Evidence

Logs, capability probe, isolated fixture, signed builds, and authenticated
release-verification metadata:

`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ConversationTitle-20260919-pxkCJa`

Artifacts and hashes are recorded in `release/manifest.json`.
