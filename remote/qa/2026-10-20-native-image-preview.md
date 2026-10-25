# Native Image Preview

## Observed Source

The user's screenshot is an existing native image-generation result, not a
command printing arbitrary Base64 and not an MCP result. The selected workspace's
cached commentary matched the screenshot. A bounded read of that same Agent's
events identified `item.completed / imageGeneration`, source item
`exec-f7d50a43-d612-4b0e-9d60-acf8fdce6815`.

The event was saved as 21 consecutive 128 KiB fragments beginning at event
93226. Their original fragment-group identity was independently checked before
reconstruction. The `result` field contains a complete PNG: 2,051,421 bytes,
SHA-256 `3c84c3fe92080709a265b06b0692b12688ec9f7b25e62b9a8e13798a006bd1dd`.
The complete original envelope is 2,740,021 bytes. The image is a generated
scheme diagram; this inspection makes no claim about its scientific validity.

Private evidence is in
`~/Library/Caches/ParallelCodexMaintenance/InlineImages-20261020/`.
Only the image, scoped source identities/checksums and local verification are
retained there. No prompt, access token, dataset or raw research log was copied
into this repository.

## Root Cause

`normalize_item` preserves the native `imageGeneration` object. The display
projection did not recognize that type or read its `result` field. When the
unrecognized envelope exceeded the event-record limit, the existing event store
fragmented the entire JSON as generic activity text. macOS drew each fragment
as work-log prose. The image had not been deleted or corrupted.

## Implementation

- Recognize native image-generation envelopes in the server projection, including
  small results that previously had no display item.
- Read the known legacy serialization in the macOS presentation projection:
  original native event identity, explicit image-generation header, adjacent
  event numbers, original 128 KiB UTF-8 block boundaries and matching projection
  metadata, bounded reconstruction, complete JSON, then strict Base64 decode.
  EventLog.append holds its per-run process/thread lock across all fragments of
  one source envelope, so these IDs cannot be interleaved by another writer.
  No generic long-string detector, command-output reinterpretation, remote file
  read or external image URL request is added.
- Keep raw transcript blocks and transport cursors unchanged. The visible image
  keeps the first source event's ID. An incomplete image keeps the same loading
  identity. Images remain between their surrounding replies, outside the
  collapsible command log, and adjacent image appearances are not coalesced.
  Repeated start/update/completion snapshots of one native call replace that
  call's placeholder, while different calls keep separate images. Identical
  large legacy fragments are not removed by the prose duplicate filter.
- Reuse NSTextView/NSTextAttachment, the same measured/drawn attachment bounds,
  a width/height-bounded aspect ratio and limited ImageIO thumbnails. Malformed,
  unsupported or oversized images retain a concise preview-unavailable state;
  the original native record remains available.
- Include image bytes in presentation-cache accounting and image checksums in
  width-specific layout keys. Invalidate the previous text-only layout schema.
  Complete native decode results use a bounded NSCache, avoiding repeated JSON
  and Base64 decode while ordinary Agent output continues.

This is deliberately native image-generation support, not a general promise
that arbitrary Markdown URLs, remote files, encoded console output, PDFs or all
MCP media are rendered inline.

## Verification

- 37 focused Python tests passed: image/event fragmentation, transcript display,
  operation output and app-server adapter coverage.
- Eight focused native tests passed before the final full gate: page-by-page legacy
  reconstruction, short final fragments, unchanged cursors/cache reload, multiple
  image identities and surrounding replies, non-media controls, malformed data,
  native updates, damaged-image boundaries, tall-image bounds, responsive
  measurement and reused-cell cleanup. The real PNG also passed dual-entry
  equivalence (complete native envelope versus old fragmented display items),
  multi-response DTO replay, stable identity and native canvas pixel checks.
- The decoded PNG from the actual source event was rendered by the production
  AppKit collection item in never-ordered windows at 460 and 1000 points. No
  production window was opened, selected or sent a message for testing.
- Final canonical client gate passed: 652 tests in 61 suites, 391.107 seconds,
  plus protocol/desktop-state contracts and native icon/resource verification.
  The gate explicitly set `PCR_INTERACTIVE_QA_AUTHORIZED=0`.
- Complete-image result caching reduced the real image's 20 repeated projection
  checks from 291.443 ms before caching to 4.222 ms in the final full gate.
  This is a local fixture measurement, not an installed-app performance claim.

### Test Invocation Correction

A direct SwiftPM filter unintentionally matched the entire transcript test
target, including opt-in mounted-window QA suites that the canonical script
excludes. Those test windows were closed by their fixtures; no production app
window, message or competition task was operated. The direct run reported four
assertions in one old mounted-scroll test (two appearances), whose reversal
expects inner ownership to resume immediately during the same wheel sequence.
This does not establish an image regression and is not being reported as a
successful interactive acceptance test. Subsequent full verification uses
`test-client.sh` with `PCR_INTERACTIVE_QA_AUTHORIZED=0`; it does not repeat those
foreground tests. No source scrolling behavior was changed for this feature.

## Release And Installation

The user subsequently explicitly authorized publication and an application
restart. macOS **0.1.464 / build 464** was formally published through the existing
authenticated Relay catalog and installed with the unchanged native updater.
Existing 0.1.463 artifacts remain immutable.

- The version-frozen release gates passed: 652 native tests in 61 suites
  (397.270 seconds), 18 strict image/lifecycle/updater tests in three suites,
  37 Python tests, protocol/desktop-state contracts, icon/resources, encrypted
  signing-store and explicitly-disabled-signing regressions.
- The independent optimized bundle reused `Parallel Codex Release Signing`;
  deep/strict native signature verification and unpacked archive validation
  passed. No signing identity, Keychain, trust or security policy was changed.
- Archive: `Parallel-Codex-macos-0.1.464.zip`, 14,830,255 bytes, SHA-256
  `3e016b18446736503412bbbc6e3fddb9481ced1955719e28920f27a56e47f785`.
  The actual installed build 463 received `available=true`; its authenticated
  one-use-ticket download matched these bytes, and its existing native verifier
  accepted the same publisher and exact staged bundle.
- Normal background restart completed at `2026-10-20T14:17:09Z` (22:17 China
  time), PID **77923 -> 48452**. No force termination or foreground activation
  was used. Loaded main-executable inode **190009353**, SHA-256
  `4a2a10cfc68749c932702e3a24b6fb00d8cffba47002df8e6227873d6344f4b9`.
  All installed bundle files match the signed publication. The installed build
  464 then correctly received `available=false` against server build 464.
- Fresh pre-exit/restart guards verified durable drafts, selections, settings,
  local queues, outboxes, accepted handoffs and original answer identities.
  Protected state hashes and original answers were retained. There were no
  pending answer deliveries. The client had no open AX windows or extra-window
  IDs; no obsolete completed-Agent selection gate or test window was used.
- pcrd **0.1.102 / PID 88104**, its launch definition, receipt-store identity
  and ten persistent owners retained their original process identities/start
  times. All fourteen previously live native Agent processes retained their
  execution receipts and process identities. Fresh run APIs advanced by **74**
  and **94** events during the update; bounded original native event reads
  independently returned six and eight new native records respectively. This
  is run-level progress evidence, not a claim that every Agent must emit during
  a short restart. The old cached runs-index counts are not used as progress.
- Relay connection remained connected; post-install authenticated update
  traffic succeeded independently of local `health=ok`. No prompt, answer,
  retry, adoption, stop or other task command was sent. Other component/artifact
  entries in the release manifest remain unchanged. Rollback protection retains
  only the prior program, not older user data.

Release/install evidence is private under
`~/Library/Caches/ParallelCodexMaintenance/InlineImages464-20261020/`, including
publication, authenticated delivery, exact-byte preparation, before/after
runtime/input snapshots, native continuity and bounded native-progress readback.
The offscreen native PNG previews above are not presented as an on-screen
installed-app pointer-workflow acceptance test.

### Backend Boundary

The client release renders the screenshot's existing large fragmented image
using the original, unchanged pcrd 102. The additive server projector repair for
small native image results omitted entirely by older backends is source-tested
but **not built, published or enabled** by this client-only update. No healthy
manager/worker or remote computation was restarted to activate that separate
change. This release does not claim general MCP, Markdown URL or remote-file
image support.
