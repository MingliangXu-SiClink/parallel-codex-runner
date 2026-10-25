# macOS work-log and contextual-action acceptance

Date: 2026-09-22 (Asia/Shanghai)
Delivered: macOS 0.1.380 (380), privately published, installed, and launched.
No pcrd, Relay, Android, Agent, or web-service restart was performed.

## Product decisions implemented

1. Keep internal preparation, polling, synthesis, and final collection out of
   the main status label. The header reports Working, Saving, Completed, or a
   genuine actionable failure; its status lane has a stable width.
2. Reuse readable operation details during refresh and reopening. Do not insert
   loading paragraphs into cached output or replace unchanged text storage.
   Updating a fixed-height inner work log no longer scrolls the outer timeline.
3. The composer follows user intent, not background RPC phases. Only an actual
   submission owns its progress indicator. Background reads cannot replace the
   send control with a spinner.
4. Stop the currently viewed running Agent from the composer. With an empty
   draft, the primary control is Stop; with a draft, Send remains available and
   a separate compact Stop control is shown. The stop RPC captures Agent/run
   identity. Enter still sends a draft; it does not invoke Stop.
5. Conversation commands stay by the title. Agent commands use a distinct
   sliders icon beside the Agent name. Save Result is next to that Agent.
   File commands remain in the file section. The duplicate inspector footer
   and its extra stop/overflow controls have been removed.
6. Preserve existing capabilities: wide replies, a shorter composer, adjustable
   sidebar/result panes, persistent result details, adjacent file review, and
   bounded, borderless work-log scrolling with edge fades.

## Work-log presentation

- Compact native icon/text rows, shared alignment, trailing disclosure arrows.
- Category summaries rather than changing event counters in the disclosure label.
- Subtle file links; ordinary answer links retain the user's accent color.
- Long commands occupy one line, with full text in the tooltip and disclosure.
- Exact timing and exit information remain in operation details. The compact
  row shows duration only for commands taking at least 10 seconds.
- Legacy command records can expand their stored text without a detail RPC or
  a fabricated unavailable-output notice.
- A native walkthrough exposed summary truncation at larger Chinese text sizes.
  The disclosure now uses the reading column instead of an undersized estimated
  width; its arrow follows TextKit's actual rendered line. Regression added.

## Verification

- `apps/macos/scripts/test-client.sh`: protocol and desktop-state checks passed;
  173 tests across 25 Swift Testing suites passed in 99.760 seconds.
- Production build passed in 38.96 seconds, signed with the existing
  `Parallel Codex Local Signing` identity. Strict/deep signature validation passed.
- Used the installed SDK 26.5 through `SDKROOT` and `PCR_MAC_SDK`. Default SDK/
  SwiftPM invocation encountered the existing macro-framework/toolchain issue;
  the repository test/build scripts with the explicit supported SDK passed.
- Actual native app walkthrough in an isolated app bundle and HOME, connected
  to the real IPC controller with a fake run manager: light and dark appearance,
  standard and largest text size, 1040-point and maximized windows, Agent
  switching, work expansion, individual operation details, independent scroll,
  file preview alongside result details, contextual menus, and quit/reopen.
- Reopening a cached read operation made only one detail RPC for that record.
- Typed a test draft and sent it into the isolated server queue. Send did not
  stop the running Agent. Removed the queued test message and stopped fake
  Agent 1 using the composer; the RPC log confirms its exact run/Agent target.
  No production prompt was submitted and no real Agent was stopped for QA.
- On the formal installed client: restored the selected SiClab conversation,
  inspected the new work rows and result controls, collapsed/reopened the work
  log, and returned to the current work log at the end of the conversation.
  Existing live usage and elapsed time continued advancing.
- pcrd PID 834 and persistent worker PID 12608 retained their original start
  times across the client replacement. The source workspace was not changed.

## Release evidence

- Relay release: `macos-release-4a290fbe2fc84c23aad73ffb60205204`, active.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.380.zip`
- SHA-256: `abefb9e296aefb4fcd6c4aaefa3d08b3a896034979fb64530e3de2a18d61bc99`
- Installed executable matches the signed build:
  `862ff7fc84b8d218771c761351a0e4daf61126ee2c50f2aaf755759a3ece754f`.
- App installed at `/Applications/Parallel Codex.app`; old app and pre-change
  source snapshot retained in
  `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/WorkLogUX-20260922-3jhCjK`.
- Private remote staging ZIP removed after successful publication verification.
- Isolated QA client and fixture server shut down after acceptance.

## Scope and remaining checks

This is a macOS presentation/interaction release, not a replacement of the
server state machine or an Android redesign. Existing backend contracts and
release versions were preserved. No numerical FPS, CPU, GPU, or memory claim
is made: layout/identity regressions and native interaction were tested, but
this round did not run a sustained production performance benchmark. Long
multi-line thought/progress text remains fully readable inside the capped log;
it is not discarded merely to match a screenshot.

The fake controller initially rejected queue replacement because its allowlist
was incomplete. Corrected the fixture and repeated removal successfully; this
was not a production queue defect. Existing historical records cannot gain
outputs that were never stored.

## References

- User-supplied ChatGPT work-process screenshot: visual hierarchy and compact rows.
- Apple, [Demystify SwiftUI](https://developer.apple.com/videos/play/wwdc2021/10022/):
  view identity and stable updates.
- Apple, [Menus and actions](https://developer.apple.com/design/human-interface-guidelines/menus-and-actions):
  contextual action placement.
- [Chatbox](https://github.com/chatboxai/chatbox): high-level component/feature
  organization reviewed; no third-party source was copied.
