# macOS Work-Process Scroll Chaining

## Scope

macOS client only, version 0.1.399 / build 399. Do not restart pcrd, stop Agents,
modify research workspaces, change device pairing, or restart web services.

## Diagnosis And Implementation

`FadingWorkScrollView.scrollWheel` previously consumed and clamped every vertical
event, including continued input and momentum at the work-process boundaries.
The outer conversation could never receive the remaining displacement.

The work process now consumes only available vertical displacement. The remaining
displacement continues in the enclosing `ConversationTimelineScrollView` in the
same frame. Top and bottom boundaries both participate. A new gesture or direct
direction reversal can return to the work process. After a handoff, streaming
content growth does not steal the current gesture or its momentum. Unphased mouse
wheel ticks are routed independently. Cancellation and view detachment clear
gesture ownership.

The outer transfer uses the existing user-scroll cancellation callback and native
clip-view bounds updates. Ordinary conversation scrolling remains unchanged.
Native scroll-wheel replay was rejected after a regression test demonstrated
that AppKit ignored momentum.changed when the outer view had not received the
original began event. A zero-displacement began seed did not resolve that case.
The final implementation does not create, rewrite, repost, or duplicate events.

Bounded height, borderless edge fades, stable row measurements, independent
disclosure, and existing reading-position persistence are preserved.

## Verification

Focused native regression: 9 tests passed, including parameterized top/bottom
residual handoff during direct input and mid-stream momentum; both themes;
precise and line-based scrolling; streaming growth; diagonal input; cancellation;
view reuse; and preservation of the original event and user-scroll callbacks.
The exact residual test sends 24 points with 8 remaining internally and asserts
16 points of conversation movement. A complete handoff/momentum sequence asserts
70 points of outer movement without moving the inner reading position.

Full client regression passed: 278 tests in 38 suites, plus client contracts,
bootstrap checks, packaged math/syntax smoke test, and native app-icon checks.
Built with the compatible MacOSX26.5 SDK; the installed command-line SwiftUI
macros are incompatible with the default MacOSX27.0 SDK.

Independent native acceptance used a private HOME and fixture IPC server.
Coordinate-based wheel input over the visible work-process panel confirmed:

- Inner bottom stayed at 1 while the conversation advanced from 0.319027 to
  0.562232; no pointer move outside the panel was required.
- Inner top stayed at 0 while the conversation moved upward from 0.515021 to
  0.271817.
- When the inner view could still scroll, it advanced from 0 to 0.248175 while
  the conversation remained at 0.271817.
- The idle position remained unchanged after input stopped. Bounded height and
  independent commentary outside the work process remained visible.

Some early accessibility-targeted scroll attempts also moved an off-screen
target into view; those exploratory captures are not used as acceptance.
The accepted coordinate captures and measured AX values are recorded in
`native-acceptance.json`. Automated input does not constitute a claim about the
tactile feel of every physical mouse or trackpad model.

The only live-scroll notification dependency calls the same `beginUserScroll`
callback used by the handoff. Reading position, near-bottom publication and
history geometry are observed through clip bounds changes. Outer geometry and
ancestor hierarchy are resolved on every transfer; no stale outer reference or
cached maximum is held. Nested handoff clamps at the entire conversation edge
without synthesizing rubber-band events.

## Publication And Deployment

Published macOS 0.1.399 / build 399 to the existing relay release catalog.
Release ID: `macos-release-72256699bea04a23b716cacbc6691c7b`.
Archive: `release/clients/macos/Parallel-Codex-macos-0.1.399.zip`.
SHA-256: `cb266a8f5b2da9f2f05ed36417e89bd06e756b795400fa61b0ab0537acf66767`.
Signature: `Parallel Codex Local Signing`, unchanged designated requirement.
All non-macOS manifest components and artifact hashes were preserved.

Installed at `/Applications/Parallel Codex.app`. The old client (PID 9699) exited
normally with Cmd-Q, preserving reading state. New client PID 25451 started at
2026-10-03 13:04:21 Asia/Shanghai; installed version and binary hash were verified.
Binary SHA-256: `9a65e9f1a26f51907f73ce1cac92019b1ff1fb89327f2dab77edf76831914610`.
The real UI restored the selected conversation, result detail layout and
GPT-6.1 Sol model; the active task remained at 4/5 candidates completed.

pcrd remained 0.1.66, healthy and paired, with its LaunchAgent definition
unchanged. Run `pcr-20261003T022623Z-6a750dd0` remained active before and after
installation. All 49 captured Node/Codex/Python process identities were preserved;
no Agent or web service was stopped. The isolated QA app and fixture server were
closed. This macOS-only release does not activate the previously staged pcrd
0.1.67 update or alter its separate idle-only deployment authorization.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/WorkScroll-20261003`.

Design consultation:
https://chatgpt.com/c/6ac082dc-a0a0-83e8-8bd8-dfe648f5fe9a

The dialogue led to an additional failing mid-stream momentum regression and
diagonal/lifecycle coverage. The implementation decision is based on local
native test results, not solely the consultation's suggestions.
