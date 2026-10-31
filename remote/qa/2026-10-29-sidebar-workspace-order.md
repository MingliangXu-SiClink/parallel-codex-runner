# macOS workspace display order

## Scope and behavior

- Previously the workspace sidebar followed the directory response order and
  had no user-owned display order. The new preference belongs to the client,
  keyed by machine and stable workspace ID, not names or array positions.
- Drag a workspace row to a top/bottom insertion line and drop to reorder.
  Hovering does not select a workspace or persist anything; cancel leaves the
  order unchanged. The context menu also offers Top, Up, Down and Bottom.
- Manual order survives directory refreshes and client restarts. Newly seen
  workspaces append in discovery order. Temporarily absent rows retain their
  saved positions. Without a manual move, the original default order remains.
- Only the presentation array changes. Server workspaces, selected Agent,
  conversation drafts, queue execution order and task execution are untouched.
  Recent conversations retain their existing chronological/pinned behavior;
  Agent numbering is not repurposed as manual display order.

## Implementation and verification

- `WorkspaceSidebarOrder.swift`: preference, private typed drag payload and
  native drop handling; `ContentView.swift`: sidebar integration and menu.
- The workspace drag type is separate from queue-message drag data and is
  declared in the app bundle. Invalid data and another machine's payload are
  rejected. Preference updates use the current list of stable IDs.
- `WorkspaceSidebarOrderTests` covers move direction, before/after positions,
  persistence/reload, refresh, new workspaces, temporary absence, machine
  isolation, and actual NSItemProvider delivery into the production drop code.
- Actual sidebar views rendered in hidden native windows at 280 and 380 points.
  Images were inspected: the moved workspace appears first, remaining rows
  retain their order and layout. Source order, selection and draft stay intact.
  This is not a claim of a real-user mouse-drag acceptance test.
- SDK 26.5 and the repository's existing test scripts were used. Full client
  contracts, 764 Swift tests in 67 suites, and native app-icon checks passed.
  Foreground interaction suites were intentionally not enabled. An initial
  ambient SDK 27 attempt failed to load its SwiftUI macro plugin; a direct test
  invocation also lacked the CLT Testing framework path. No code workaround or
  toolchain replacement was introduced; the existing script supplies the paths.
- The text-based sidebar contract was adjusted only for the view's internal
  visibility, allowing its actual implementation to be tested. The Dynamic
  Type stability requirement is unchanged.

## Delivery

macOS 0.1.494 was signed with the existing release identity, formally published
through Relay and downloaded through the existing authenticated update path.
The native updater verified the archive and normally restarted only the client.
Loaded binary SHA256:
`d149665ec449f36d784c732709822b207a9d5c2b8901146fdbcd9f723366aad5`.

Actual reopened UI exposes the new workspace row identities and restores the
same selected conversation and unsent draft. A subsequent optional context-menu
inspection was stopped when the user took control; no sorting or navigation was
performed in the user's live window. Native rendering and drop tests above are
the verified interaction evidence, not a claim of manual mouse-drag acceptance.

Before/after verification preserved daemon PID 1262, all four original owners,
all nine live native process/thread/turn/message identities, and input/config
hashes. Seven native logs advanced; two had no new bytes in this short interval.
Drafts, outbox, accepted handoffs, queues, answer drafts and stopped states were
unchanged. No pcrd, Relay, Android or plugin update/restart was performed.

The verified local duplicate ZIP and this release's remote upload staging were
removed (15,197,432 bytes each). Published archives, native updater rollback
bundle, source, tests and audit evidence remain. No research files were removed.

Private build, tests, native images and before/after continuity evidence:
`~/Library/Caches/ParallelCodexMaintenance/SidebarOrder-20261029/`.

## 495: transient neutral drag feedback

The user observed a blue insertion line still visible after reordering. In 494,
each row stored its own insertion-side state and manually cleared it through
DropDelegate callbacks. The screenshot demonstrates residual feedback; the exact
callback sequence that produced it was not captured. The previous tests covered
order and provider delivery, but not the lifetime of visible drag feedback.

495 removes the insertion line and the manual enter/update/exit state entirely.
SwiftUI's native `onDrop(isTargeted:)` owns target presence, including drag exit
and cancellation. The target row receives a neutral 5.5-percent foreground tint,
without a border or additional layout space. A completed drop clears feedback
synchronously before asynchronous payload decoding and row reordering. The
original before/after destination calculation and stored order are unchanged.

Regression checks exercise the production drop receiver with a real provider,
asserting the highlight is gone before commit, including rejected payloads.
Native light/dark pixel checks compare idle, hover and released states: the
hover is neutral and visible, and released pixels must exactly match idle,
including both row edges. Deployment/real-window checks are recorded after
completion in `SidebarHighlight-20261029` under the private maintenance cache.

### 495 validation and delivery

- The full native suite passed: 765 tests in 67 suites, including the new
  light/dark pixel and synchronous-feedback-clear checks. No foreground QA
  window was opened. `git diff --check` passed.
- Signed 0.1.495 was formally published and downloaded through the paired,
  authenticated update channel. The native updater verified the existing
  signing requirement, normally quit only the client and relaunched it without
  activation. The installer recorded build 495, `launched`, no rollback.
- The actual loaded binary in client PID 68211 matches the signed build:
  `6f5cddce7cf7b0a64d3c507774b38a2a51ab2e28bdef44dde7465edeeb8cd3b0`.
  Strict signature verification and the manifest/archive hash checks passed.
- Before/after checks preserved daemon PID 1262, all four worker owners and all
  nine live native process/thread/turn/message identities. Six logs advanced;
  three had no additional bytes in this short interval. Source inputs,
  configuration hashes, user-stopped states and the old pending command receipt
  were unchanged. No daemon or Relay restart or Agent intervention occurred.
- Workspace order, drafts, outbox, accepted handoffs, queued prompts and answer
  drafts matched exactly. The real reopened window restored the same workspace,
  selection and unsent input. Optional live drag testing awaits permission; no
  workspace was moved or message sent in the user's window. Native automated
  rendering/drop tests are not represented as manual interaction acceptance.
- A preflight health read timed out at 10 seconds; the post-install read returned
  `ok` in 4.96 seconds. This sidebar release does not claim to fix service latency.

The verified duplicate local archive and this release's remote upload staging
are removed after delivery. The published archive, rollback app and compact
audit evidence are retained; research data and existing releases are untouched.

### Authorized live drag attempt

The user subsequently authorized moving one workspace and restoring its order.
On installed 495, three computer-use drag attempts targeting the visible
`PCR performance acceptance` workspace produced no observed reorder. The
current conversation remained the fine-hand workspace with an empty composer.
Window activation did not change the result. A further coordinate-scaling
diagnostic was blocked because the user resumed control, so interaction stopped.
This does not establish whether native dragging failed to start or the app's
drop path rejected it; it is not a successful live drag acceptance check.

The final accessibility order matches the initial order, so no restoration was
necessary. `drag-before.json` and `drag-after.json` in the private evidence
directory capture the unchanged durable order, drafts and background execution
identities. No messages, adoption, stopping or service restart were requested.

## 496: preview the actual insertion gap

The user rejected the 495 interaction: whole-row highlighting suggested nesting
while the invisible upper/lower-half rule selected two different insertion
positions. Removing a residual line did not resolve this semantic mismatch.

The sidebar now has one reorder session, with a snapshot of the original row
positions. Neighboring rows move around an invisible source row, leaving a
full-row-sized insertion gap. Hit-testing uses the original anchors, not the
moving rows. Drop commits the same destination already previewed, with no final
mouse-up recalculation. Preview never writes preferences; exit/cancel restores
the original order, while the existing persistence and context-menu moves stay
unchanged. This is flat ordering only, not a workspace or file move.

A small native drag source supplies the existing row image and an AppKit
completion callback, including Escape/outside cancellation. It separates click
from drag without blocking the event loop. The destination is the entire list,
including its gaps; rows are not folder-like drop targets. See Apple's native
[drag lifecycle documentation](https://developer.apple.com/documentation/appkit/nsview/begindraggingsession(with:event:source:)).

Tests exercise preview-to-commit identity, stable repeated hover, top/bottom and
both directions, exit/re-entry/cancel, click suppression after a drag, and the
actual sidebar in hidden native windows at 280/380 points in light/dark mode.
Rendering compares idle, gap and cancellation without using real conversations.
Release and real-client evidence is recorded separately under
`~/Library/Caches/ParallelCodexMaintenance/SidebarGap-20261029/`.

### 496 validation and delivery

- Both the native suite and complete client script passed: 767 tests in 67
  suites, protocol/desktop contracts and native icon checks. Light/dark gap and
  cancellation renders were inspected at both sidebar widths.
- Signed 496 was published through the existing Relay channel, downloaded with
  the paired client, verified by the native updater and normally restarted.
  The installer journal is `launched`; the loaded binary SHA256 is
  `d16ceaefafd4c67be4bbbe0f327254a3469893c2288e84f76116e3b6908b574d`.
  Normal quit took longer than the UI tool timeout; the old client subsequently
  exited on its own. No force termination was used.
- Daemon PID 1262, five owners and all 14 then-live native process/thread/turn/
  message identities were preserved. Thirteen native logs advanced during the
  update. Drafts, outbox, handoffs, answers, queues and saved display order were
  unchanged. No service or Agent was restarted. One read-only health call still
  timed out: this sidebar release is not a connection-performance fix.
- Real-window automation did not produce an observed reorder, including a
  longer drag and a coordinate-scale diagnostic. The conversation and composer
  stayed unchanged, with no residual line/highlight. This is NOT a successful
  physical drag acceptance check. No order restoration was necessary.
- Follow-up regression now uses the production native drag view's actual
  `begin`/`end` closures and measured row frames, and checks native hit testing.
  Its first diagnostic incorrectly supplied receiver-local coordinates to
  `hitTest`, which expects the receiver's superview coordinates. After correcting
  that test, the production view receives the hit and all sidebar tests pass.
  Temporary source experiments were discarded; released production-source
  hashes are unchanged. Only the stronger test was added after packaging.
- That diagnostic full run also hit one unrelated prompt-receipt timing failure
  (the two earlier full runs passed). The unchanged test and the strengthened
  sidebar suite were rerun together: 11 tests in two suites passed. No timeout
  was relaxed and no failing assertion was removed.
- The local duplicate ZIP and this version's remote upload staging were removed
  (15,212,652 bytes each), retaining the published archive and updater rollback
  app. No research files or other releases were cleaned.

### Independent interaction review

A [Pro review](https://chatgpt.com/c/6ae2e5c8-38fc-83ee-9294-e20a1c491072)
also identified the hit-test coordinate mistake. Its suggested distinction is
retained: a measured gap and successful hit test do not prove that an actual
system dragging session reaches the destination and commits. Neither SwiftUI
event interception nor sparse automation events is established as the cause
of the unchanged live order. No speculative production workaround was added.
The user was asked to check one physical drag in installed 496; that acceptance
remains pending. If it fails, the next diagnostic is the first missing event
in the source/session/destination chain, not another visual redesign.

### User-confirmed physical drag acceptance

The user subsequently confirmed for installed 496: "可以，落点与空位一致".
This closes the pending manual check for successful movement and agreement
between the insertion gap and final position. The earlier automated drag
attempts remain inconclusive, not retroactively passing. No additional client
restart, workspace reorder, or backend intervention was performed for this
confirmation.
