# Per-Agent Native Questions

## Scope

Released versions: pcrd **0.1.95**, macOS **0.1.438 / build 438**.
This work is confined to Parallel Codex Runner, not SiCLAB or research projects.
Publication and activation are separate. Running user tasks must not be stopped,
resubmitted, adopted or migrated to enable this feature.

Private verification evidence:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/AgentQuestions-20261016/`.
All model/tool fixtures use temporary workspaces and Codex homes. Native tests use
the actual Codex CLI 0.159.2, but an isolated loopback Responses fixture, not a real
provider or real user turn. No production prompt was submitted for acceptance.

## Root Cause And Implementation

The existing app-server adapter rejected every native server request with
`-32601`, including `item/tool/requestUserInput`. It also did not enable Codex's
Default-mode question feature for new private threads. Adding a client form alone
would therefore not have completed a native request.

- Only `item/tool/requestUserInput` is now supported by the adapter. Its response
  is returned on the original JSON-RPC channel, not as a new prompt or turn.
  `features.default_mode_request_user_input` is scoped to the private thread on a
  supported CLI. Global configuration, execution mode, approvals and sandboxing
  are unchanged.
  This is restricted to registered runs whose originating client advertises the
  `agent_questions` capability. Negotiation is transport metadata, not a prompt
  edit: old outboxes keep their exact body, client/command IDs and fingerprints.
  It survives encrypted Relay forwarding and durable command replay. The run's
  ownership marker persists the negotiated channel before native startup.
  Legacy desktop/other clients and standalone non-interactive CLI runs do not acquire a tool
  that could wait forever without any answering client. The unshipped 0.1.93
  candidate was rejected at this compatibility boundary; its wheel/runtime are
  retained separately, never published or activated.
  A first 0.1.94/437 publication completed at 2026-10-16 07:55:15 UTC, without
  activating either component. The subsequent mixed-client audit found that a
  registered run alone did not prove its frontend could answer. The final
  0.1.95/438 pair supersedes that publication with explicit client negotiation;
  the earlier immutable artifacts and publication evidence are retained under
  `published-0.1.94-437/`. No running user process was changed in either phase.
- Each Agent has a private transactional SQLite question store, and the actual
  adapter holds a non-inherited `flock` lease. Request identity includes a random
  native connection generation and native RPC ID, together with the original
  thread, turn, item and question payload. Reused RPC IDs cannot inherit answers.
- Request lifetime (`open`, `resolved`, `invalidated`) and answer delivery
  (`none`, `queued`, `sending`, `sent`, `unknown`) remain independent. Native
  cleanup is not treated as proof that an answer was received or adopted.
- `runs.agent` exposes additive question snapshots and summary counts.
  `runs.question` reads the exact original receipt; `runs.answer_question` commits
  an immutable answer. The existing command journal can recover a crashed
  `started` entry only for this independently transactional action, with the
  original fingerprint still checked. No other mutation gets broader retries.
- macOS maintains one process-wide durable draft/answer outbox, separate from
  visible workspace, Agent and window lifetimes. A confirmed choice is written
  before transport; transient storage failure blocks delivery and automatically
  retries that same choice after recovery. It is never silently called saved.
  Quit-time persistence is owned by the application, including after the last
  window and its weak model reference disappear; an undurable confirmed choice
  cannot silently leave with the process.
- A missing answer response triggers original-receipt reconciliation before any
  resend. The low-level transport's immediate mutation retry is disabled only
  for this action, including encrypted Relay routing. A different saved answer
  is a conflict, not last-writer-wins.
- Questions use an in-conversation form and an Agent-specific marker. Options,
  grouped questions, custom text and secret-field rendering are supported with
  no default consent, global modal, batch warning or navigation change.
  `isBlocking=false` does not change execution state or resource admission.

## Recovery Contract

A client disconnection, client restart or outer IPC restart does not expire a
still-live native request. Confirmed choices resume independently of the current
page. Other Agents continue processing their own events and may complete while
one Agent has an unanswered question.

Native owner/connection loss is different: choices remain stored, but old answers
are not replayed to a replacement request or converted into a new conversation
message. A crash after `sending`, including after a response write but before
the durable `sent` update, leaves an explicit unconfirmed delivery state.
This is one immutable logical answer, not a claim of cross-process exactly-once
native delivery or proof that a model adopted the decision.

Old clients can ignore the additive fields. A new client connected to an older
daemon retains ordinary reading and task controls. An unavailable question API
is local to the question outbox; it does not disable the workspace or composer.
Existing native workers are not hot-patched into the new interactive adapter.

## Fault Acceptance

Backend cases cover client/reader reconstruction, original thread/turn identity,
two Agents with different answers, continued output during an unanswered request,
concurrent identical and conflicting submissions, invalid choices, grouped/free
text, native cleanup racing with answers, user stop, terminal/finalizing runs,
both daemon-journal crash windows, native RPC ID reuse, abrupt owner death before
and after response write, lease exclusivity, private files and symlink refusal.

Client cases cover durable-before-delivery, duplicate windows, immutable choices,
cold restart with the original command ID, lost acknowledgement without another
mutation, late/stale/wrong-execution snapshots, branch-bound cache restoration,
reversed disk-write order, corrupt storage preservation, transient storage
recovery without another confirmation, and native offscreen form rendering at
420 and 980 point widths. The windowless quit test invokes only the delegate's
early refusal path; no actual app termination or global desktop-state flush is
requested. No foreground UI automation or real app termination
is used. Native images were inspected for readable text, controls and overlap.

The real CLI loopback acceptance starts two native threads and two original
turns. Agent B receives B and completes while A remains pending. The disposable
IPC server is then rebuilt with the same command receipt store; A keeps its
original connection/thread, receives A and completes. Duplicate RPC submissions
still yield only four provider-fixture requests, with no new user prompt.
The observed native questions are nonblocking; the adapter tests also cover
blocking questions. This is not an online production-model acceptance test.

The final regression also corrects two isolated fixtures, not production history
or state: a session-recovery test now supplies its temporary Codex home rather
than opening the user's evolving global database; the cold-selection test seeds
the canonical observed queue as well as the visible projection, so a concurrent
refresh cannot erase an incompletely constructed fixture's selection.
The strict cooperative-pool run also exposed a blocking socket poll in the new
test peer, not the production outbox. That fixture now runs its blocking I/O
through the existing dedicated transport executor. Its stopped test helper and
sampled stack are retained; no real app, daemon, Node or Agent was stopped.

## Verification And Release

Final frozen verification passed:

| Scope | Result |
| --- | --- |
| Backend source | 1156 tests and 289 subtests passed |
| Installed 0.1.95 runtime | 1155 tests and 289 subtests passed |
| macOS 438 | 536 tests in 55 suites passed |
| Strict cooperative pool | All 12 question/outbox tests passed |
| Native source and installed builds | Each passed the real two-Agent/IPC restart loopback scenario |
| Package bytes | All 338 wheel payload files match the independent installed runtime |

The one installed-build deselection is the repository bootstrap-location test,
which correctly runs only against source and passed there. No runtime behavioral
case is omitted. Protocol/desktop-state contracts, the generated protocol check,
icon checks and packaged math/syntax rendering also passed. No foreground QA or
real online-model/user turn was used. Source, tests and build hashes are bound to
the final component artifacts; results from 93 or the first 94/437 publication
are not substituted for the final verification.

Final immutable artifacts:

- `release/pcrd/parallel_codex_runner-0.1.95-py3-none-any.whl`
  SHA-256: `8f99c4fb52591b6875ec1d5bb5e36fdbac330ce91dff480d4ea71bc7d5a5827c`.
- `release/clients/macos/Parallel-Codex-macos-0.1.438.zip`
  SHA-256: `d89fb78feca6357730cf458f323e89d72040f1984a8b401888b40cc032aa8e17`.
- The final ZIP was extracted and rechecked for version/build, strict/deep code
  signatures and bundled rendering resources. It contains no Finder metadata.
  The existing signing authority and certificate root are unchanged:
  `Parallel Codex Local Signing`, `7cf98a08b97b137a2952b8f39c71531b62f6403f`.

Publication completed at **2026-10-16 08:22:00 UTC**. The official Relay update
catalog has build 438, and the real paired controller's standard update-check
endpoint, queried as the currently installed build 436, returns **0.1.438** with
the exact final ZIP hash and size. No download ticket, installation or restart
was requested by this check. `release/manifest.json` now advertises 0.1.95/438;
its SHA-256 is `27c8427934acdc0571755a0338f08fd3af32ebd7d0ca847b16efcb7c6544f8cf`.
The pcrd installer, backend install metadata, signing identity and all unrelated
release components/artifact records remain unchanged.

The 0.1.95 runtime is installed in its independent version directory for byte
verification and later activation. This does not update the running service.

## Production Boundary

The read-only pre-publication snapshot found a healthy pcrd **0.1.92**, macOS
**0.1.436**, and three running task groups at 2026-10-16 07:17:58 UTC. The snapshot
records the LaunchAgent definition, actual process start identities, client
loaded executable inode/hash, pairing and command receipt store identity.
No installer, service-definition change, global restart or task control is part
of the publication entry point. The previously paused deployment automation is
not resumed.

Post-publication read-only verification at **2026-10-16 08:24:14 UTC** confirms:

- Global pcrd is still **0.1.92**, healthy, PID **73487**, with the original
  LaunchAgent definition/runtime arguments, pairing and command receipt store.
- The loaded official app is still **0.1.436**, PID **4569**, executable inode
  **184684656**, with the exact original binary hash and process start identity.
- All **40** baseline Node/Codex processes are still running with their original
  process identities. Three real task groups remain active; no global operation
  was initiated by this release work.
- No task was stopped, paused, adopted, discarded, restored or resubmitted. There
  was no real service/client restart, live-state migration or research-file edit.

Therefore **published does not mean activated**: the new interactive execution
path has not been enabled in the currently running user environment. Future
activation must wait for a safe deployment opportunity and recheck the actual
0.1.95/438 targets, messages, queue state, signatures and receipt identity. A
previously prepared 437 archive must not be mistaken for the final 438 update.
The paused follow-up was not resumed or retargeted by this feature implementation.

An independent embedded-browser discussion examined actual protocol findings,
native continuation, unknown-delivery windows and owner lifetimes. Its agreement
is design feedback, not evidence of correctness; acceptance comes from the
artifact-bound tests above. The final discussion screenshot is retained as
`interaction-design-95-438-final.png` in the evidence directory; earlier discussion
screenshots are also retained.
