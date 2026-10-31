# Durable Successful Model Catalog: pcrd 110 / macOS 479

## Contract and Cause

The user requires a committed successful catalog to remain immediately readable
across restarts and discovery failures. Discovery updates that catalog; it is not
permission to read it. A complete new success replaces the old catalog, rather
than unioning obsolete entries or replacing it with the selected model alone.

Before this change, the backend read path synchronously probed the CLI and joined
the refresh lock even when its successful catalog already existed. The macOS
options cache was memory-only. The earlier 109 negative-cache repair fixed a
real defect, but did not establish this stronger contract. The first internal
failure of the historical 108 incident remains unobserved; this release does not
claim to identify its unique initiating cause.

## Implementation

- `codex_model_cache.py`: locate committed success using existing CLI identity
  metadata, serve it without probing or joining discovery, refresh expired
  results in the background, and atomically advance a catalog revision only on
  complete usable success. Empty, hidden-only, failed and incomplete discovery
  retain the last success. Existing execution prewarming is unchanged.
- `codex_models.py`, `plugin_runtime.py`, `remote/controller.py`: load models and
  revision from one snapshot; return an opaque account/provider/config/CLI
  context through existing capabilities and model-options APIs. Read refresh
  activity before loading the snapshot. Do not promote a selection-only fallback
  into discovered catalog data.
- `CodexModelOptionsCache.swift`: persist metadata in UserDefaults under
  `parallel-codex.model-options.v1`, partition by machine/context/model, fence
  late old-context and old-revision responses, preserve other windows' records,
  and never persist transient refresh activity.
- Composer and default-settings loaders retain committed choices while fetching
  updates. Background discovery no longer changes an explicitly selected
  reasoning effort. Existing selected models, counts and accepted message
  parameters remain unchanged. Model metadata is not inference authorization.

## Regression Evidence

Private evidence: `~/Library/Caches/ParallelCodexMaintenance/CatalogDurable-20261027-C3PGwU/`.

- `red-tests.log`: new persisted-success tests fail on the preceding behavior.
- `backend-final.log`: 1,252 tests and 322 subtests pass. Run `pytest -q tests`;
  bare repository-wide collection collides with Relay's separate test package.
- `installed-tests.log`: 43 tests pass against the actual installed 110 wheel,
  including real loopback PcrdServer/PcrdClient calls while the probe is blocked
  and the persistent-worker-survives-owner-shutdown regression.
- `native-final.log`: 715 tests in 63 suites pass. `protocol-tests.log` separately
  passes protocol and desktop-state checks; its repeated native run has three
  100 ms budget violations within one existing 40-branch painting test
  (105-140 ms). The unchanged targeted rerun passes with a 21.43 ms maximum in
  `native-timing-recheck.log`. This intermittent timing uncertainty is retained,
  not represented as every repeated suite passing.
- `archive-smoke.log`: packaged math/syntax rendering passes from a fresh process
  after moving the frozen source tree, excluding a source-resource fallback.
- Isolated cases cover restart restoration without a successful CLI probe,
  reads during a blocked refresh, complete replacement, failed/empty retention,
  scope changes, late responses, durable revision ordering and multiple windows.
  No real competition input or production outage was used as a fault fixture.

## Release and Activation

| Component | Version | Artifact SHA-256 |
| --- | --- | --- |
| pcrd | 0.1.110 | `1493306f9ec621d37a7fdc035337d73a3c6c6d54051a30d0b60e51d94ef7095e` |
| macOS | 0.1.479 / 479 | `d44c8c42e7cc6c4f7d401534644634d667d09e2f7733fd07c45a76a9ea8c54c2` |

Immutable artifacts and the updated manifest are in `release/`; Relay 21,
Android and the daemon installer artifact are unchanged. macOS release
`macos-release-e4d1159c92a64e0987c65532d1f6a2a4` was formally published at
09:19:18 UTC, authenticated-downloaded, and accepted by the unchanged native
signature verifier under Parallel Codex Release Signing.

Manager 109 PID 5118 exited normally. LaunchAgent now selects the independent
110 runtime; live capabilities report 110. The 478 client exited through its
normal application menu, then its native updater installed/relaunched 479 in
the background. `last-install-result.json` reports installed=true and no
rollback. Loaded binary/inode matches the published archive; binary SHA-256 is
`91831112090dc2af3173ab018d6c1e55911a7686d5165199877fd331f1ed706f`.

## Live Acceptance

The new API returned the existing revision 0 in 0.527 s while refreshing, then
revision 1 with refreshing=false in 0.781 s. Subsequent cached reads were
0.101/0.152 s while another background refresh ran. All eight concrete visible
IDs remain available. Actual installed candidate-menu AX and screenshot show
the concrete choices without the previous default/failure rows. Persisted
context records exist for both candidate/synthesis model contexts; refresh
activity is absent from stored success. No real account switch or production
discovery failure was forced; those boundaries are covered by isolated tests.

All eight original workers retain their PIDs, start identities and checkpoints.
The active run `pcr-20261027T080538Z-fcfe0f1d` retains worker 4065, native PID 4134,
original thread/turn/message identity, parameters and zero recovery count.
At 09:30 UTC its current native tool was an explicit 900,000 ms sleep started at
09:24:19 UTC; no-output during that wait is not a failure. Post-wake continuation
is recorded separately below after observing real original-session events.

User-stopped synthesis Agents 10/11 stay killed. Original in-doubt stop receipt,
follow-up queue bytes, answer drafts, outboxes, handoffs and workspace settings
are preserved. No input was replayed, Agent stopped/retried, result adopted,
research directory moved or token/pairing changed. Previous runtimes and the
native updater's previous program remain available for program-only rollback.

### Final Readback

At 09:39:41 UTC, `acceptance-20261027T093941Z.json` records seven genuinely new
native events after the manager-switch offset: the original timed sleep
completed, command `exec-e4495629-9a9f-496c-ac56-5494d3de74ef` started, updated
and completed with exit code 0, followed by new reasoning. The original native
PID, thread, turn, message and zero-recovery receipt still match. The installed
client subsequently displays that new activity and the Agent's next normal
timed wait. This proves original-session continuation, not every remote job's
health or research completion.

Opening the existing configuration again, without selecting anything, updates
the installed client's two durable model-context entries from revision 1 to
revision 3, each with eight concrete models and no persisted refresh flag.
`client-catalog-refresh-acceptance.json` records this readback; original model,
effort, counts, drafts and message identities are unchanged.

Only this turn's generated frozen build tree, duplicate smoke bundle/artifacts
and exact remote release-staging directory were removed, approximately 719 MiB
locally. Immutable published artifacts, verification records, rollback programs,
all existing workspaces and user modifications remain. `cleanup.json` records
the scope. The design review was consulted at verified maximum Pro mode; its
agreement was not used in place of source, test, runtime or native UI evidence.
