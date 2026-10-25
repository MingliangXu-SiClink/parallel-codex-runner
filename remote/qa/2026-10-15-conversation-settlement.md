# macOS 433: accepted message settlement

## Scope

The user requested repair and publication of the macOS client. Build 433 includes
the previously verified, unpublished 431 background-reading and 432 saved-session
provenance fixes, plus the remaining accepted-message reconciliation defects
described here. Those two earlier candidates were never update-feed releases.

This request does not resume the user-paused deployment or authorize installation
and restart. No pcrd code, research data, live desktop-state file or real task is
edited. The running official client and daemon must be reported separately from
the published update.

## Root causes

The original saved-session submission had already been accepted, completed and
rendered. Client 430 nevertheless retained its accepted handoff because it treated
the server's resolved native-session parent as a conflicting explicit source.
The shared validator from candidate 432 fixes that contract mismatch while still
checking the original workspace, command/client, prompt, session and explicit
source where one was actually supplied.

Three additional client defects can outlive that validator repair:

- The pending-message projection unconditionally adds a current bubble even when
  its exact accepted run is already displayed, duplicating a real question.
- Settling that receipt re-enters the new-run navigation path, carrying the current
  answer back into history, clearing transcripts and choosing another Agent.
- An accepted-run disk write captures its page before waiting for the serial I/O
  queue, then can replace a newer warm page/selection or durably write old content.

These are identity and state-transition bugs, not a reason to resend the question
or remove genuine repeated prompts from history.

## Repair

- One shared proof determines whether the displayed run is the retained accepted
  destination. It requires the correct machine/workspace and the same destination
  validator used for durable handoff. Only that temporary pending representation
  is folded into the existing turn, including the read-only Agent selector and
  surface status. Equal words under another command remain a separate message.
- When the exact accepted turn is already open, settlement preserves its current
  answer, historical entries, chosen Agent, navigation generations and next draft.
  It records the current readable state rather than navigating to the same run.
- The serial disk operation re-reads and validates the newest warm page immediately
  before writing. Subsequent hydration fills missing entries without rolling back
  newer memory. The receipt is consumed only after a successful durable write.
- Late receipt metadata cannot decrease the workspace revision. Genuine identity
  conflicts and failed writes keep the original receipt; sending permissions are
  not relaxed merely because a page can be read.

No text-based history deduplication, manual live-state repair, warning masking,
real-message replay or server-side result adoption is used.

## Reproduction and verification

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/ConversationSettlement-20261015`

- `red-tests.log` reproduces the duplicate bubble, duplicated historical turn,
  answer/Agent/navigation reset and stale warm cache: four tests, 15 issues.
- `red-durable-and-native.log` adds the missing Agent selector and demonstrates
  stale immediate cold-cache readback even after the initial warm-cache repair.
- Focused regressions assert the complete answer and selected Agent, exact count
  of real prompts, preserved identical earlier questions and a separately queued
  identical later command. They check disk state before any debounced flush,
  delayed cache I/O, write failure/recovery and real identity conflicts.
- The native AppKit timeline is rendered in an unshown, non-key window. Before
  and after settlement it contains one current prompt, one complete current answer
  and the legitimate same-text historical question. `native-held-receipt.png` and
  `native-settled.png` preserve the actual rendered cells, not just a loading label.
- Existing slow/fragmented/lost receipt, restart, rapid navigation and original
  source/Agent preservation cases remain part of the full client suite. An actual
  private production run/receipt fixture is validated offline; its text is not
  copied into this report or sent to external collaboration.

An anonymized follow-up was sent to the existing embedded-browser technical
discussion. Its answer again showed "unable to load this response". No readable
independent response or agreement is claimed.

## Publication procedure

Use stable source/test/resource hashes, the full client suite, strict background
reader regressions, generated protocol checks, release build, the existing signing
certificate and extracted-archive rendering checks. Publish only the new immutable
macOS archive through the existing Relay admin update catalog, verify the catalog
and authenticated client check endpoint, then atomically replace only the local
macOS manifest entry. Keep every non-macOS component and artifact unchanged.

The maintenance helper `release_client.py` is version-specific and does not invoke
the paused activation script, updater installation, client restart or scheduler
restart. Final measurements and actual publication status are recorded below only
after those checks complete.

## Final verification and publication

- `client-final-tests.log`: **485 tests in 53 suites passed** in 258.214 seconds.
  Standalone protocol and desktop-state contracts and all application-icon checks
  passed. The private captured production receipt was supplied to its offline
  validator test. No real user turn was submitted.
- `strict-tests.log`: **35 tests in five suites passed** with
  `LIBDISPATCH_COOPERATIVE_POOL_STRICT=1`. These cover background reading/cache,
  directory transport and destination identity; legacy blocking fake sockets are
  not scheduled on a single cooperative worker.
- The full-suite warm fixture made 50 workspace switches and 400 complete-answer
  selections while disk/health reads were held: p95 0.893 ms, maximum 1.250 ms.
  All 40 native branch answers and their three-page histories appeared; slowest
  native paint was 70.420 ms, within the unchanged 100 ms fixture assertion.
  These controlled measurements are not a guarantee for arbitrary system load,
  never-fetched content or unbounded history.
- The native settlement PNG was visually inspected: it preserves the two genuine
  same-text questions in separate turns, one full current answer and Agent 2's
  selection. There is no extra sent-message bubble or lost answer.
- Sorted source/test/script/resource, branding, package and Info.plist hashes
  match before tests, after tests and after release construction. Manifest hash:
  `5d51024e162f2bac3cf9e0ed10e0902082e8383a79e8b705d209aa62e4f7ce39`.
- Built and extracted applications identify **0.1.433/build 433**, pass
  deep/strict signature validation and packaged math/syntax rendering. Authority:
  `Parallel Codex Local Signing`; the designated requirement retains published
  certificate root `7cf98a08b97b137a2952b8f39c71531b62f6403f`.
- Immutable published archive:
  `release/clients/macos/Parallel-Codex-macos-0.1.433.zip`.
  Size: 14,270,546 bytes. SHA-256:
  `86c6bdca0756d62ef48a870afce07cc34ed4bd0d8c13d8ffd8e7466e413623af`.
  Signed executable SHA-256:
  `d9bfaf6e7db79dec20be324a6590550a42ed396fbe605cb4f63564bfdbbdd66a`.
- Actual Relay publication completed at **2026-10-15 04:12:44 UTC** as
  `macos-release-938dc68e9ac04914a953ac639320e49c`. The admin catalog reports build
  433 non-revoked with the exact archive hash. The existing authenticated
  `controller.http` check endpoint also reports that same update available to the
  real paired client. No download ticket, updater installation or restart was
  requested by this verification.
- Only the local manifest's macOS component/artifact changed. Its resulting
  SHA-256 is `7aaf0ac685352d6370434f7ca57d6a12d768657b2a1715f106f5e4fa50194bc1`.
  Every non-macOS component/artifact remains unchanged and hash-verified, including
  the pcrd 0.1.88 wheel
  `3b4233e08fd4facba85d7a65829379638f7f311445b71a229b602f287174f55e`.

Read-only observations before and after publication confirm the official loaded
client remains **0.1.430**, PID **67550**, executable/loaded inode **184059749**;
the paired actual daemon remains **0.1.86**. All **23** baseline Node processes
retain their original PIDs and start times. The original accepted-handoff
identities are unchanged and the outbox remains empty. The
`pcr-0-1-88-430` deployment automation is still **PAUSED**.

This is a verified publication, **not installation or activation**. The unchanged
running 430 process has not acquired the new reconciliation behavior. Automatic
settlement on the corrected client is covered by isolated restart/handoff tests;
the retained production receipt was not manually edited, consumed or resubmitted.
