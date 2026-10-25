# Conversation Loading Coherence and Remote Project Layout

## Scope and Causes

macOS 0.1.450 fixes navigation and cache coherence, not the historical-turn
footer's wording or visibility. Two deterministic races were reproduced in
isolated native/loopback tests:

1. An "open latest" navigation retained a fixed run ID while a newly discovered
   continuation advanced the conversation index. The old in-flight response or
   disk record could then remain visible as an earlier turn. Latest navigation
   now retains its intent and resolves the current destination after suspension;
   explicit historical navigation remains pinned to the chosen run.
2. An index-only projection already carried the requested run ID, causing the
   disk-cache bootstrap to be skipped even though the transcript and history
   were empty. Complete disk history now hydrates this case before the network
   summary read, merging with any live output that arrived during disk I/O.

Cached installation checks the exact requested run, machine, workspace and
navigation generation. Late responses cannot replace the latest destination.
History paging no longer keeps navigation pending; loaded history is retained
and only truly uncached pages load progressively. The fix does not promise
instant cold-network history and does not hide genuine historical navigation.

The user's screenshot alone does not establish which specific callback caused
that occurrence. The regressions reproduce and cover these concrete causes;
source/test success is not evidence that an older installed client is fixed.

## Remote Layout Policy

Backend 0.1.99 removes the unconditional `.pcr/<run>/agent_<index>` instruction.
New schemes follow user/project naming. Existing schemes continue in place;
another turn does not imply another directory. Local workspace isolation and
loopback restrictions remain unchanged. Remote isolation is reserved for real
concurrent write conflicts. Existing live execution directories are not moved.

Shared organization, movement, deletion and deployment operate on the authorized
real targets, with one designated executor. In a parallel candidate batch the
default executor is candidate 1; in a single-Agent continuation it is the selected
Agent. Other candidates/synthesis inspect or propose rather than repeat the same
mutation. Existing completion evidence and active references must be checked.
This is execution guidance, not a new OS sandbox or an exactly-once guarantee
for arbitrary SSH commands. No remote folder was moved/deleted by this release.

Run and physical-Agent identity remain metadata. The existing instruction
resolver injects the policy through initial/resumed/retried candidate and
synthesis paths, explicitly superseding older PCR namespace guidance. Already
executing native turns are not interrupted or rewritten to inject new guidance.

## Verification

- 594 native macOS tests in 58 noninteractive suites passed, including the five
  new navigation/cache regressions, existing cross-workspace warm switching,
  explicit history navigation, draft/message delivery, lifecycle and updates.
- The six affected suites also passed with the strict cooperative pool: 85 tests.
- Protocol/desktop-state contracts, branding, offline signing-store and disabled
  signing gates passed against the same frozen macOS source inputs.
- Native test windows stayed offscreen. No real competition prompt was submitted,
  adopted, stopped or replayed. The interactive-only suites were not run.
- A first backend full-suite invocation used the private evidence directory's
  inherited umask 077, causing a file-mode expectation to fail (100600 versus
  100644); the test subprocess was corrected to its normal umask 022, without a
  product-code workaround. The final source/installed results are recorded below.

Private logs, source hashes, exact bundle manifests and delivery evidence are in
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/LoadingCoherence-20261018/`.

## Publication and Delivery

- macOS 0.1.450/build 450 was published to the actual authenticated Relay catalog
  at `2026-10-18T03:31:24.387191Z`, release ID
  `macos-release-ac648d4784ca4b729fdb85f72303d428`.
- The 14,790,917-byte signed archive has SHA-256
  `5960fd68e2d267339930e35d1e03374141283d3fbc7cbd5a250320ac1f1bdebd`.
  It retains the existing `Parallel Codex Release Signing` identity. Strict
  native signature, publisher, archive/resource and exact-file checks pass.
- The actual installed 447 controller reports 450 available. A supported
  authenticated ticket download matches the published hash/size; the ticket
  was not persisted. The unchanged native 447 verifier accepted that download
  and every staged file matched the frozen signed bundle.
- Backend 0.1.99 is in the release manifest, with a 1,172,149-byte wheel and
  SHA-256 `1f5c11888a7e3f2f5a62f08bf34ccf23ecd341a18a2b0ae8e3d476f5906fef43`.
  Its only changed Python module relative to the immutable 98 wheel is `app.py`.
  The independently installed 99 runtime uses the same pinned dependency
  versions as 98, passes `pip check`, and matches every wheel module byte.
- Final backend source tests: 1204 passed and 289 subtests passed. Installed
  runtime tests: 1203 passed and 289 subtests passed; only the source-tree-specific
  vendored-location check was deselected for the installed-package run.

The macOS application has not been restarted or installed by this work. At the
native observation it remains 0.1.447/PID 57024. AX returned zero open windows;
the existing process was not opened or focused just to obtain a UI acceptance
image. This is not claimed as a live 450 rendering of the user's original
screenshot. The signed update is available through the application's updater.

No Keychain/trust setting or update-verification rule was changed. No remote
directory migration, task replay, adoption or computation-throttling change was
performed. Backend management activation is separately recorded below.

## Backend Activation

The manager switched from 98 to the independently verified 99 runtime at
2026-10-18 11:36:04 Asia/Shanghai. LaunchAgent program identity, loaded runtime
modules and capabilities agree on 99; its PID is 55700. The previous manager
exited normally before the replacement started. The safety gate accepted the
healthy detached-worker boundary and rejected nine unsafe fixture states.

All nine original detached owners retained their process start times and
execution runtimes. No owner was replaced. The original receipt-store identity,
pairing and persisted message/draft/queue state were retained. At 03:38:17 UTC,
both originally active native Agents retained their thread, turn, workspace,
input and process identity and had 36 and 13 new native events respectively
after the online baseline. Events include completed tool execution, reasoning
progress and an explicit sleep, rather than only projected `running` state.
The existing Relay connection was authenticated and connected, and a supported
update check succeeded through the new manager. No real task command was sent.

The observer initially expected the venv path in `ps` argv; macOS exposes a
Homebrew Python application trampoline there. The observer was corrected to
correlate LaunchAgent program/PID, daemon arguments and loaded runtime module
paths. This was not a daemon failure and did not cause a second activation.

Existing 98 owners/native turns keep their original execution environment;
the new remote-layout guidance is applied by new 99 executions, not injected
into live turns. This release-boundary check does not claim to have audited
every competition's remote computation. Full evidence is in
`backend-activation-result.json` and `backend-continuity-verification.json` in
the private directory above.

## Additional Background Refresh Race

Final review after immutable 450 publication found a second entry point to the
latest-turn race: a background latest refresh can have no explicit navigation
target. Its second `turns.get` could complete after an index advance and roll
the visible run back. A held-response loopback regression reproduced both the
run rollback and erroneous historical-footer state on 450.

The 451 correction checks the current selected conversation's latest run even
when the explicit navigation target is absent; stale errors are rejected by
the same check. Explicit historical navigation remains pinned. The new test
passes after the fix. The published 450 archive is not overwritten; 451 is a
separate release, with its frozen gates and delivery evidence under
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/LoadingCoherence451-20261018/`.

Final frozen 451 verification: 595 tests in 58 noninteractive native suites
passed, plus 86 tests in the six affected suites under the strict cooperative
pool. Protocol/desktop-state contracts, app icon, offline signing-store and
disabled-signing gates also passed. The source contract was extended for the
background-latest guard rather than weakening the runtime regression.

## Final 451 Delivery

- The actual authenticated Relay catalog published 0.1.451/build 451 at
  `2026-10-18T03:49:28.627316Z`, release ID
  `macos-release-9859a8f328834885bea7a77e5b4ac189`.
- The signed archive is 14,791,231 bytes, SHA-256
  `ffd652c03cc554ee616afb648bee556856f7a859ad810166c9e49ee5ebc06b37`.
  Its publisher remains `Parallel Codex Release Signing`; strict native
  signature, archive and resource verification passed without a trust change.
- A fresh check through the original paired controller returned 451 available
  to the installed 447 client. The authenticated download matched its exact
  hash/size. The unchanged native 447 updater accepted and staged the archive;
  every staged bundle file matched the signed release. The one-use ticket was
  not persisted. The release manifest preserves backend 99 and other components.
- Final observation at 03:49:51 UTC still showed loaded client 447/PID 57024
  with matching binary bytes and no open AX windows, and manager 99/PID 55700
  with the existing authenticated Relay connection. No client restart or
  installation was attempted. Users receive the client fix after applying 451
  through the existing updater; this record does not claim live 451 UI acceptance.

The requested embedded-browser collaboration tool was unavailable in this
session; no external-browser fallback or unverified partner consultation was
used. Diagnosis and acceptance above are based on the actual local regressions,
frozen builds and authenticated delivery evidence.
