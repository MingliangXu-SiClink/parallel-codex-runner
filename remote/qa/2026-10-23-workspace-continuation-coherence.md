# Workspace Continuation Coherence, macOS 0.1.469 and 0.1.470

## Scope

Client-only correction for submitting a follow-up to a completed Agent, leaving
its workspace, then returning. No task was submitted for production testing;
there is no task restart, adoption, account migration, daemon replacement, or
research-file modification in this release.

Private timestamped evidence is under
`~/Library/Caches/ParallelCodexMaintenance/WorkspaceCoherence469-20261023/`.
Release gates bind their results to the exact source hashes. The baseline is the
formally published and installed 0.1.468, not the repository's older Git HEAD.

## Reproduced Causes

Four new regressions produced eleven failed assertions against the old source:

- Workspace selection cleared the visible submission before restoring the durable
  outbox. The completed source page could paint without the new question until a
  later refresh. Default re-entry could also select a different source Agent.
- Offscreen acceptance persisted a bare destination summary without carrying the
  original branch history or advancing the workspace head, then settled its
  receipt. Foreground and offscreen acceptance therefore produced different pages.
- A bootstrap considered cached bodies but omitted known destination metadata
  after body eviction, allowing the cached completed parent to become current.
- Cache hydration could overwrite a fresher projection even in the current
  navigation generation. A delayed registry response could also rewind a newer
  head before that head's body was cached.

These are client state/ordering bugs, not evidence of lost native execution.
`red-regressions.log` preserves the failing scenarios; test fixtures use isolated
loopback/offline transports, not production competition messages.

## Correction

- Restore the pending question and its source branch in the workspace swap,
  before network refresh. Explicit Agent/history navigation remains explicit.
- Persist the accepted destination's input and source history before retiring its
  receipt, regardless of which workspace is visible. Offscreen settlement must
  not navigate the foreground workspace.
- Reconcile heads by scoped continuation lineage and authoritative registry
  revision; preserve known destination metadata independently of body eviction.
- Merge summary freshness and transcript/history coverage separately. A newer
  partial summary does not erase already known history, and old cache hydration
  cannot replace a fresher visible page.
- Cold entry hydrates the bounded workspace cache before selecting an old parent.
  Truly uncached data still requires a read; the fix does not fabricate history.

## Regression Evidence

The final canonical native gate passed 679 tests in 63 suites, followed by 174
focused tests in six suites under a strict cooperative executor. They include held
acceptance, offscreen/foreground settlement, repeated workspace changes, cold
restart after receipt consumption using an offline socket, delayed registry and
cache results, body eviction, preserved explicit history, and unchanged delivery
identity. Native screenshots show the question and source history together.

The first broader run was not green: a newly added cold-reopen test asserted
before asynchronous local disk hydration, and the native paint gate was just
over 100 ms. The cold test now distinguishes an empty loading frame from an
incorrect parent and awaits local hydration without a network. The native
readiness fixture now uses the production five-part transcript context identity
instead of rebuilding its collection for every Agent. The 100 ms threshold was
not relaxed. The final full run measured 23.6 ms maximum across 40 complete warm
branches in five workspaces with three history pages each and health held; the
focused gate measured 17.1 ms for the same workload.
Earlier diagnostic and failing gate metadata remain in the private evidence.

## Runtime Boundary

Before installation, `system.capabilities`, `controller.status`, and
`followups.list` answered, but bounded `system.health` and `runs.list` timed out.
This is recorded separately, not relabeled healthy and not used to justify
restarting any task. A read-only sample did not establish its exact cause.

The legacy shell AX observer returned no windows while CUA visibly observed the
real main window. It is not accepted as proof that the app was closed. Client
installation instead requires a fresh CUA main-window/composer/modal check,
the real extra-window catalog, durable input/answer/queue checks, original
worker/native process identities, the native signed updater, and normal
non-activating quit/relaunch. No signature checks are weakened.

## Delivery

- Formally published build 469 using the existing `Parallel Codex Release Signing`
  identity. The authoritative server catalog changed from 468 to 469; the backend
  component remains 0.1.105.
- Published archive SHA-256:
  `5f0bd9f50e179bbb9b227ce704e6dbad933b7c956e31179dba86a72f00d15e52`.
- The authenticated public update check failed with `IpcError`; no ticket was
  issued or replayed. Contemporary daemon logs also show Relay TLS EOF/reset
  failures. These observations do not prove that the local manager read timeout
  and the public network failure have one cause.
- Native staging used the locally built archive whose bytes and signing metadata
  match the formally published server catalog. The unchanged production native
  verifier accepted it. This is not recorded as a successful public download.
- Native installation and non-activating normal restart succeeded. Actual client
  PID changed from 56966 to 94058; the loaded executable inode is 191758994 and its
  SHA-256 is `630b3c8c4ba406696ccf6bc2a9f1e59cea6b05079d1e0fd5912fb3386e25e781`.
- All 11 original worker identities and 14 original native Codex process
  identities remained unchanged. Protected drafts, answers, queues, settings,
  outbox/handoff maps, daemon PID, LaunchAgent and receipt store identity matched
  before/after. This proves update continuity, not each Agent's research health.

## Installed Workflow Blocker Observed In 469

Actual installed-client interaction is **not yet accepted**. The new real main
window starts, but cannot populate its workspace directory while the existing
daemon's manager reads are unavailable. Post-install bounded `system.health` and
`workspaces.list` both timed out at 10 seconds. CUA confirmed that startup first
showed loading, then an empty workspace presentation; no production prompt was
submitted to test around it.

Source inspection identifies an additional cold-start dependency:
`AppModel.refresh` awaits the full `system.health` response before assigning
`workspaces`. Desktop persistence retains navigation/drafts but not a workspace
directory projection. Existing conversation bodies therefore cannot be selected
through the normal sidebar until that directory arrives. The isolated cold
conversation test starts with a known workspace; it does not cover this earlier
directory gate. This boundary must not be hidden behind the successful 679/174
test counts or the successful binary update.

`RemoteController._health` calls manager maintenance under its manager lock;
`workspaces.list` also asks that manager for run overviews. Their exact live
blocking operation is not established. No daemon restart, private-state rewrite,
fabricated health response, or task interruption was used to get a green result.

## Cold-Start Closure (470)

The 469 main window eventually populated through its ordinary read path. A later
10-second health probe still timed out. This rules out interpreting the earlier
empty display as evidence that the user had removed the workspaces, and does not
establish a deadlock or a single cause for all slow daemon requests.

470 removes this navigation dependency without touching the executing daemon:

- The existing per-window desktop state now retains a small, machine-scoped
  workspace directory projection. It contains navigation identity and root path,
  not current availability, permissions, worker health or execution settings.
- Startup restores that directory and its cached conversation before awaiting
  full health. Older installations without a directory migrate only remembered
  workspace identities with matching machine/cache keys and consistent recorded
  workspace roots. They do not enumerate every historical run into the sidebar.
- Continuation lineage and the existing conversation ordering choose the cached
  head, not file modification order. Input, answer and source history are rendered
  together. Navigation metadata never authorizes a server mutation.
- A successful full directory replaces the saved projection, including a known
  empty directory. Acknowledged workspace removal removes the saved entry.
  Delayed disk hydration cannot overwrite authoritative state or user navigation;
  older registry revisions cannot roll directory membership backward either.
- Connection failure is not rendered as an authoritative empty workspace list.
  Cached entries remain readable, with unverified state in their help text;
  genuinely unavailable server entries retain their removal action.

New fixtures start an empty AppModel from production-format persisted navigation
and conversation bodies, with and without the new directory field. Full health is
held while the real native timeline must display the saved answer and history.
Additional cases cover old-schema continuation ordering, cross-machine rejection,
authoritative empty directories and delayed disk responses. No production prompt,
answer or task is used as a test message.

The first 470 focused run passed its functional assertions but failed the existing
100 ms offscreen-paint gate once at 100.759375 ms. That threshold is unchanged.
The first contract gate still required the old literal `workspace.available`
branch; its expression now also recognizes the explicitly cached-reading branch
while continuing to require removal for genuinely unavailable entries. Both
attempts are retained in the private evidence directory.

Final frozen-source 470 gates passed: canonical native **683 tests / 63 suites**
(399.069 seconds), strict-executor focused **178 tests / 6 suites** (264.730
seconds), contract checks, app icon, signing-store and unsigned-build rejection.
The full native suite's slowest of 40 warm branch paints was 86.649417 ms; the
100 ms assertion was not relaxed. The cold empty-model tests passed both legacy
and persisted-directory variants with health held and no task command sent.

Publication, installation and installed-workflow results are recorded below only
after their independent gates complete. Slow daemon manager reads and the public
Relay update route remain separate observations, not claims of restored execution
health.

### 470 Delivery And Installed Interaction

- Formal signed server catalog advanced from 469 to **470**. Archive SHA-256:
  `9a71f3c65a56b558b2d2a7492f9c7bcfb2531dfd276edec1fc17a9d5750dcbb6`.
- The authenticated public check again failed before ticket issuance. The unchanged
  native verifier staged the locally built archive after matching its full bytes
  and signer against the formal server catalog. Public download is **not passed**.
- Normal background quit/relaunch installed **0.1.470**, PID **14666**, loaded inode
  **191812570**, executable SHA-256
  `29fe05fa2560ea97c48c5092dfda88b01b98eb65202a13d7dcee0c5a52d388db`.
- All **11 original workers and 14 original native Codex processes** retained
  their PID/start identities across this installation. Protected drafts, answers,
  queues, settings/navigation and message handoffs were preserved; pcrd and the
  receipt identity were unchanged. No task was submitted, stopped, adopted or
  recreated; no data was rolled back.
- CUA inspected the actual installed main window. Before authoritative health
  completed, the remembered fine-hand workspace already displayed its newest
  follow-up, earlier question/answer and composer. Its sidebar correctly identified
  saved/unverified workspace metadata instead of claiming no workspaces existed.
- Navigated fine-hand -> memory-state -> fine-hand using real sidebar controls.
  On return the same newest follow-up and saved history remained present; the
  composer remained present; neither the erroneous earlier-turn nor newer-task
  banner appeared. The original workspace and Agent selection were restored.
  A native screenshot was visually checked, not a QA window presented as production.
- These are post-navigation observations, not a video proof of every intermediate
  frame or an exhaustive server-history audit. Pending-submission/late-acceptance
  races are covered by isolated fixtures, not production test messages. Current
  live progress, manager read latency and public Relay delivery remain separately
  unverified; this client fix does not claim to repair the daemon's slow-read cause.

Private evidence: `WorkspaceCoherence470-20261023` under the maintenance cache,
including frozen test logs, publication metadata, native staging/install receipts,
before/after continuity and `installed-ui-acceptance.json`.
