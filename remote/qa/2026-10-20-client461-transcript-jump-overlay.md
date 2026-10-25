# macOS 0.1.461: Floating Transcript Jump Control

## Scope And Root Cause

This release fixes the jump-to-latest control appearing on an opaque full-width
strip below the transcript. It does not change conversation navigation, history
hydration, result adoption, question delivery, or execution-worker behavior.

`AgentTranscriptView` already drew the circular button in a bottom-aligned
`ZStack`, but its native timeline was inside a `VStack` with an additional
48-point canvas whenever `followsLatest` was false. That sibling reserved an
opaque footer instead of allowing the transcript to continue beneath the button.
Showing and hiding the button also changed the native viewport height.

The old static contract explicitly required this footer. A hosted viewport test
copied the same reserved-lane composition, so it did not reject the bad layout.

## Change

- The native timeline now occupies the complete transcript region in the
  existing `ZStack`; the 48-point canvas and its height constant are removed.
- The existing 32-point circular button, shadow, bottom offset and jump action
  are retained. Its hit shape is explicitly circular. No full-width hit target,
  background, safe-area inset or replacement footer is added.
- The native scroll view keeps its existing opaque canvas, painting and motion
  ownership. Search, sticky Agent selection and context-keyed reading memory
  are unchanged.
- The obsolete static contract and copied test footer are corrected. A new
  regression hosts the production `AgentTranscriptView`, not a copied layout.

## Reproduction And Validation

Before the fix, the new native regression failed with 24 issues across four
parameterized cases: a 520-point host contained only a 472-point native viewport,
bottom-side hit tests did not reach the transcript, and following the end changed
the viewport back to 520 points.

The final regression checks 420/980-point widths, standard/extra-large text and
dark/light appearances, giving eight native configurations. It verifies full
viewport height, hit testing on both sides of the button, retained native scroll
view identity, reading offset and follow-state preservation over three repeated
show/hide cycles. The offscreen windows never become visible or key. Actual
native screenshots were inspected in both appearances; text continues behind the
circular control without a footer.

Final-source gates passed:

- Protocol/static contracts and application-icon validation.
- 629 non-interactive native tests in 59 suites, including the new regression.
- 32 focused tests covering overlay geometry, viewport stability, motion
  ownership, client lifecycle and update verification.
- Encrypted signing-store and explicitly-disabled-signing regressions.
- Optimized signed build, strict deep signature verification, immutable archive
  and exact existing-publisher verification.

## Publication And Installation Status

macOS **0.1.461 / build 461** was formally published to the authenticated Relay
catalog at **2026-10-20 00:22:20 UTC**. Server readback matched the archive,
version, size and existing `Parallel Codex Release Signing` publisher.

- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.461.zip`
- Size: `14819636` bytes.
- SHA-256: `14de0bade8cc974121b555051b36374a5010e882a456901695359e15fa067688`
- The actual 460 installation's authenticated update check returned
  `available=true`; its official download matched the published bytes.
- The installed client's native update verifier accepted the archive and staged
  the exact signed bundle. This is **prepared, not installed**.
- The active local client remains **0.1.460**. No automatic client quit, restart
  or application replacement was performed for this publish request. The normal
  software-update restart remains the activation step; installed UI acceptance
  is not claimed here.

Backend source and the previously published 0.1.102 artifact matched their
existing evidence. No backend was built, installed or enabled. Before/after
read-only observations retained pcrd PID 88104, the receipt-store identity and
the same two active run/worker identities. Local `health=ok` and Relay state are
recorded separately; the authenticated update download independently exercised
the business connection. This was not a full remote-computation health audit.

No task input, answer, adoption, cancellation, retry, worker stop, remote-job
operation or research-file change was issued. Existing user drafts and answers
were not rewritten. All five changed native files are enumerated by the frozen
source delta; unrelated worktree changes were left intact.

Timestamped evidence, expected-red output, final gates, native screenshots,
catalog receipts and prepared-bundle verification are in the owner-only directory
`~/Library/Caches/ParallelCodexMaintenance/TranscriptOverlay461-20261020/`.
