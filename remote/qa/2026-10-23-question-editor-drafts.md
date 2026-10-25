# macOS Question Editor Draft Continuity

## Scope

Client-only repair for the reported almost-black answer-submit button and text
being cleared during question editing. Baseline: installed macOS 0.1.470;
candidate: 0.1.471. No daemon, Agent runtime, task execution, answer-delivery
protocol, adoption, queue, or account changes.

Private build and verification evidence:
`~/Library/Caches/ParallelCodexMaintenance/QuestionDraft471-20261023/`.

## Reproduced Causes

1. The answer binding wrote synchronously to the existing `AgentAnswerOutbox`,
   but read an immutable `AgentQuestionPresentation` captured by the timeline.
   Parent projection and collection updates can be deferred. Displaying the
   current value and then replaying a captured empty presentation reset the
   actual AppKit field/editor to an empty string even though the same outbox
   entry still held the complete draft. An unchanged empty-to-empty snapshot
   did not reproduce it; not every refresh clears input.
2. The ordinary collection update plan retired/reinserted the suffix after a
   changed prefix. A streaming/history insertion before a stable question could
   retire that question's native input tree. Reuse of the same collection item
   did not preserve its field editor, focus, or Chinese marked text. The native
   coordinator regression reproduced those losses without opening a window.

The first strengthened run failed five assertions across these two native
regressions. The submit action already read the live outbox and fixed its body
before delivery; this was not a reason to change delivery or resend answers.
An empty answer remains legitimately disabled. Its near-black styling was an
independent readability defect, not evidence of a missing delivery receipt.

## Changes

- The interactive form observes and reads the existing scoped outbox directly.
  There is no second draft store and no default-value write on appearance.
  Scope includes machine, workspace, run, physical Agent, request, and connection.
  Historical or closed presentations cannot become editable through this path.
- Submit eligibility and text/option selection use the same current draft.
  Intentional empty edits are retained. Existing command IDs, persistence,
  commit, acknowledgement, and native delivery semantics are unchanged.
- Stable editable-question rows use the existing ID-based collection batch
  path. Real moves use Swift `CollectionDifference.inferringMoves()` and native
  `moveItem`, retaining the field editor rather than deleting/recreating it.
  The existing in-flight-update guard keeps one source/target snapshot for each
  batch and coalesces later targets; no new polling or deferred-input mechanism.
- The inline submit button has explicit readable enabled/disabled appearances
  in both color schemes, while retaining native Button disabled semantics.

## Verification

- Native stale-snapshot replay: text survives a current-to-old presentation,
  width change, durable reload, and submission through the original action to a
  loopback fixture. The original answer identity and exact body are retained,
  with one delivery. No production answer was submitted.
- Native production coordinator: inserting output before a question, then
  moving that question across existing rows, preserves the same control/editor,
  first responder, text, and active Chinese marked-text state.
- Multi-question draft edits, sibling preservation, deliberate clearing,
  Agent cell reuse, scope separation, and historical read-only behavior.
- Dark/light appearance at 420 and 980 point widths. Offscreen rendered PNGs
  include empty and typed forms, with readable disabled and enabled buttons.
- Full canonical native gate: 687 tests in 63 suites passed. Signing-store,
  no-signing, contracts, and app-icon gates passed on the frozen candidate.
- Final strict cooperative-executor focused gate: 106 tests in 10 suites
  passed, including native question input, inline layout, collection/scroll
  publication, work rendering, client lifecycle, and signed updater regressions.

The first broader strict focused run included two legacy cases normally excluded
by the canonical script. It failed their five assertions: the composer fixture
still expects a 16-point font, although the released composer uses 15 points;
the work-scroll fixture still expects same-gesture recapture after reaching an
edge, contrary to the released whole-gesture ownership contract. Those source
files and policies are unchanged from 470 (see the 2026-10-21 composer-polish and
2026-10-20 client462 work-scroll audits). The failed log is retained. The focused
gate excludes only those two named legacy cases; it does not suppress any
question-editor, insertion, move, draft, delivery, or lifecycle test.

The hidden SwiftUI fixture does not expose its submit button through its AX
tree. Button appearance and eligibility, plus the production submit closure,
were tested; this is not a claim of a production button click or real Agent
receipt. The user's live question was not answered or used as a fixture.

## Delivery

- 0.1.471 / build 471 was signed with the existing `Parallel Codex Release
  Signing` identity, strict-deep verified, and published to the formal Relay
  catalog. Server readback matched version, build, hash, size, and signer.
- Archive SHA-256:
  `7feac81957b5bb305109d8b0dd4cb6698ac0b8248f2144ff08b9be66d2558d8a`.
  Size: 14,969,620 bytes.
- The existing authenticated controller's update check returned an IPC error.
  No token refresh, authentication change, or verification exception was used.
  The local copy of the exact formally published archive passed the unchanged
  installed native updater's signature, metadata, and full-bundle checks.
  This is not a successful public/download-path verification.
- The user explicitly authorized the client update. A fresh CUA observation
  found the main window, an empty composer, no active question editor, no PDF
  preview, and only an empty accessibility help window. Durable drafts,
  answers, accepted handoffs, outboxes, local queues, and service queues were
  checked before normal termination. The existing native updater installed
  0.1.471 and relaunched the client in the background at 2026-10-23 01:08 UTC.
- Client PID changed from 14666 to 34139. The installed full bundle matched the
  signed publication and the loaded executable inode matched the installed
  file. Loaded executable SHA-256:
  `f78faeb798c8ee58cb9df41469f3b3f667fda054949a65aac4bb478b68106ebf`.
- All 11 original worker processes and 14 original native Codex processes
  retained their PIDs and start identities. Daemon PID, runtime, LaunchAgent,
  receipt store, drafts, answer bodies, queues, and navigation were unchanged.
  No forced termination, task command, answer submission, or user-data
  rollback occurred. The manager health query remains unverified; process
  continuity is not a claim of complete task or remote-compute health.
- Post-install CUA readback confirmed the restored main window and selected
  workspace, empty composers, no modal, and no update failure. Agent activity
  labels were still marked as cached at this read, so this is a UI-restoration
  check rather than proof of fresh native execution progress. Evidence is in
  `client-install-result.json`, `runtime-install-before.json`,
  `runtime-install-after.json`, and `post-install-window-check.json` in the
  private release evidence directory.

Loaded 471 bytes and the client-only restart are verified. No production
question was answered to test the repair; real question acceptance is not
claimed from fixture tests or the update alone.
