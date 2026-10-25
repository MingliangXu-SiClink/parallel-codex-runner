# Background-ready workspace and Agent reading

## Scope and deployment boundary

Client candidate: macOS 0.1.431 (build 431), based on the 430 source.
No pcrd code, protocol, research workspace, running Agent configuration, or
real user submission is changed by this work. Existing pcrd 0.1.88 publication
is not activated by these checks. The user-paused 0.1.88/430 automation stays paused.

This record distinguishes implementation, isolated verification, a signed
candidate package, update-feed publication, installation, and actual execution.
No official-client installation/restart or update-feed publication is performed
by this implementation task.

## Root changes

- Warm workspace navigation reads memory synchronously. It no longer waits for
  a scan/decoding of every disk-cache file or a health/network refresh.
- Per-machine/workspace/run background readers survive workspace navigation.
  Every Agent is paged to the current end, not only four Agents/four pages.
- Each branch advances independently; a held branch or prior-history request
  does not hold other branches behind a whole-workspace commit barrier.
- Existing background metadata polling schedules incremental body reads and a
  terminal transition gets a follow-up even when received during another read.
  Unchanged completed data and immutable prior history are not repeatedly fetched.
- Pure transcript merging, normalization, and raw presentation preparation run
  outside MainActor. MainActor validates identity and publishes the prepared result.
- Cache keys retain machine, workspace, conversation, run, and Agent identity.
  Late work pages cannot regress a cursor, late answer pages cannot overwrite an
  independently advanced answer, and removed contexts cannot be resurrected.
  A replaced artifact is fetched from byte zero before publication; a failed
  replacement fetch leaves the previous complete answer readable.
- Capturing visible content during a background merge does not persist stale
  visible metadata over a newer background summary. Drafts, original commands,
  selection confirmation, queues, stop intent, and safety gates are unchanged.
- Presentation retention uses its existing 32 MiB cost budget instead of an
  arbitrary twelve-entry limit. Structured run memory and disk retention remain
  bounded by their existing policies; no history is truncated to fit a cache.

## Acceptance design

Use private fake Unix sockets with the real client transport, structured cache,
pagination, restart hydration, and native AppKit timeline. Never send a real turn.
Cover five workspaces/eight Agents, more than four work pages, complete paged
answers, three-page prior history, stalled sockets/disk, rapid switching, failed
reads/recovery, terminal answers without new events, late concurrent receipts,
stale visible metadata, and deletion while reads remain in flight.

Assert exact displayed answers/cursors/status, unchanged draft/destination,
zero unexpected mutation actions, bounded observation concurrency, and a changed
nonblank native frame. Windows stay offscreen and never become key windows.
The timing fixture accounts for one-time AppKit/TextKit startup separately.

Hot cached reading is not a claim that never-fetched or evicted history is already
in memory, nor that an offline client knows new server state. Cold data and genuine
connection/permission failures retain honest loading/recovery behavior. No receipt
or execution-completion guarantee is inferred merely from readable progress.

Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/InstantReading-20261015`

## Strict concurrency verification

`strict-final-tests.log`: 27 tests in four suites passed with
`LIBDISPATCH_COOPERATIVE_POOL_STRICT=1`, including both ordinary read failure and
replacement-first-page failure. Fifty workspace swaps plus 400 complete-answer
selections had p95 0.949 ms and maximum 2.440 ms with cache I/O and health held.
The real offscreen timeline displayed all forty branch answers; slowest warm
native appearance was 68.810 ms. A new terminal update reached an already drained
branch while a different branch remained blocked (test completed in 125 ms).
These are controlled fixtures, not a production-latency guarantee for arbitrary data.

## Final verification and candidate package

- Full background client command: `PCR_MAC_SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk PCR_MAC_BUILD_JOBS=2 PCR_INTERACTIVE_QA_AUTHORIZED=0 bash apps/macos/scripts/test-client.sh`.
- `client-final-tests.log`: **467 tests in 52 suites passed**, plus standalone
  protocol/desktop-state contracts and all icon checks. Focus-changing interactive
  suites were not run. No real user turn was submitted.
- Full-suite timing fixture: p95 1.720 ms, maximum 2.450 ms for the fifty workspace
  swaps/400 answer selections; forty native branch appearances were all below
  100 ms (maximum 83.451 ms). The native PNG was inspected and contains the correct
  workspace-4/Agent-8 answer and three prior-history pages, not a loading surface.
- Generated conversation protocol check passed. Client source, tests, resources,
  scripts, package configuration, and Info.plist hashes match before tests and
  after the release build. Sorted hash-manifest SHA-256:
  `6d7a44a2d7a60806a4e47504fc3c8e9e3651c63c43c09c408c7f0f37c421c91b`.
- Release build and extracted ZIP both identify **0.1.431 / build 431**.
  Deep/strict code-signature verification and packaged math/syntax rendering passed
  on the built app and again on the extracted archive. Signing authority is
  `Parallel Codex Local Signing`; the designated requirement matches the existing
  published client certificate root `7cf98a08b97b137a2952b8f39c71531b62f6403f`.
- Candidate ZIP (not update-feed publication):
  `InstantReading-20261015/Parallel-Codex-macos-0.1.431.zip` in the evidence directory.
  SHA-256: `913215f33583c3a7c568e570659e11471e8c35b01e0517633fcdfbc8fe473b9f`.
- Signed executable SHA-256:
  `c8cebfc4f3e46d07df693d08d1718d4aa2a7ac01adedfe487330410e26cef50e`.
- Existing pcrd 0.1.88 wheel is unchanged:
  `3b4233e08fd4facba85d7a65829379638f7f311445b71a229b602f287174f55e`.

The local release manifest still advertises macOS 0.1.430/build 430; this task did
not publish a new feed entry, install a client, restart the official client,
activate a scheduler, or stop/replay a user Agent/Node process. The existing
`pcr-0-1-88-430` automation was rechecked as **PAUSED**. Its source-byte gates
must not be bypassed to install a different client candidate.
