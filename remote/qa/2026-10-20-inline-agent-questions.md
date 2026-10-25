# macOS 0.1.465: Inline Agent Questions

## Scope and Cause

The accepted design makes questions part of the Agent's conversation, not a
second question list below the transcript. Previously the native question's
plain-text projection remained among replies while a detached disclosure list
used generic receipt headings such as "Agent 2 received the answer". That split
lost the visual connection between the original question, choice and receipt.

This release is client-only. Question delivery, retries, task queues, adoption,
native execution and remote computation are not changed.

## Implementation

- Project a typed question message at its original native item identity, scoped
  by run and physical Agent. Do not infer interactive questions from Markdown.
- Coalesce repeated snapshots of the same request, but retain different native
  request IDs even when their question text is identical. A request preceding
  its text page retains its first-observed in-turn anchor until the page arrives.
- Use the same avatar gutter and 40-point body start as ordinary replies.
  Pending questions expose native radio choices, optional custom answers and
  explicit submission. Answered questions show a meaningful question title or
  the complete question, the chosen answer and a secondary delivery receipt.
- Keep one native hosting tree through draft and receipt changes; share the
  existing inline-control click guard. Details expand within the message row,
  preserve the reading anchor and do not create another scrolling viewport.
- Carry question metadata through local cache and continuation history.
  Authoritative coalesced history retains annotations only within the known
  native turn and an unambiguous exact question span. Ambiguous or unrelated
  prose remains unchanged. Older history without native question metadata is
  not guessed into interactive questions.
- Preserve original machine/workspace/run/Agent/request/connection/answer IDs.
  "PCR received" is still distinct from a confirmed native Agent receipt;
  unconfirmed delivery never becomes "received" merely because it was saved.

The approved composer, execution menus, single primary action and work-log
scrolling sources are byte-identical to release 0.1.464.

## Validation

- Canonical non-interactive native gate: **661 tests in 62 suites passed**,
  395.184 seconds. Protocol and desktop/bootstrap contracts, icon checks,
  signing-store and signing-disabled tests also passed.
- Focused strict-cooperative gate: **61 tests in 7 suites passed**, 31.014 seconds.
  Covers question identity/order, duplicate snapshots, draft/restart delivery,
  receipts, cache/history, original answer IDs, lifecycle and jump overlays.
- Actual AppKit/SwiftUI components rendered at widths 420 and 980 with standard
  and extra-large text. Tests assert body alignment, complete row height, native
  radio hit-test bounds, no nested question scroll view and stable cell identity
  across receipt updates. These were never-ordered native windows, not HTML
  mockups or production task messages.
- Development gates caught and corrected fixed-font scaling, a needless helper
  rename and a transcript dependency leaking into the standalone bootstrap
  compilation. Final frozen-source gates passed after those corrections.

## Publication and Installation

| Stage | Verified Result |
| --- | --- |
| Source and tests | Frozen source delta: 12 production files, 3 test files, version plist |
| Signed build | Existing offline Parallel Codex Release Signing identity; unchanged publisher; deep strict verification |
| Formal publication | Private Relay catalog latest version/build 0.1.465/465 |
| Authenticated updater | Installed 464 saw update 465; one-use download matched exact size and SHA-256; native staging verifier accepted it |
| Native installation | Normal background restart, PID 48452 to 76563; no forced termination or focus change |
| Loaded program | Executable inode 190093282 and SHA-256 below matched the new installed bundle |
| Post-install channel | Current build 465 reports no newer update |
| User data | Original draft/navigation, local/service queues and immutable answer identities/bodies retained; no user-data rollback |
| Execution continuity | pcrd 0.1.102 PID 88104, 10 owners and all 14 original native executions retained; no worker restart |

Archive: 14,899,447 bytes, SHA-256
`9240bf94e38bb6d9f20b58cfd12f80be3febfc191e459869bb34c80cbd221997`.

Loaded executable SHA-256:
`8e10edffa0299f1ecacf44ef6d35efcee0db4d14428a94fc0fcb7d1d1179dc63`.

The two active original runs advanced by 22 and 91 API events across restart.
A later bounded native read returned 44 and 23 native events respectively.
These are per-run continuity observations, not blanket per-Agent or remote-job
health claims. No production question was answered or resent for acceptance.
There were zero actual open client windows before and after restart; none was
opened or focused for QA. Visual acceptance is therefore the native component
evidence plus verified installed bytes, not a claimed live production click.

Private evidence:
`~/Library/Caches/ParallelCodexMaintenance/InlineQuestions-20261020/`, including
frozen gate records, four native width/zoom previews, the integrated answered
question preview, publication/staging/install records and bounded native events.
The pre-existing small-image backend projector changes remain source-only;
this release neither builds nor enables a new pcrd runtime.
