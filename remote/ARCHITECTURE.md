# PCR Remote Architecture

## Product contract

PCR Remote is a native remote controller for PCR instances running on macOS or
Linux machines. It is not remote desktop software and the relay server is not a
Codex executor.

```text
Android App ---- WSS/E2EE ----> Relay Server <---- WSS/E2EE ---- pcrd (macOS/Linux)
macOS App  ----- WSS/E2EE -----------^                          |
Local TUI  ---------------- local Unix socket ------------------+
                                                               |
                                                     PCR core / Codex workers
```

Each `pcrd` machine remains the source of truth for its workspaces, per-workspace
run settings, runs, Agent state, event logs, diffs, Codex sessions, and finalization. The server
keeps identity, device, routing, acknowledgement, encrypted offline-envelope
metadata, and separately administered signed Android release artifacts. It never
stores project source or plaintext PCR commands. Product feedback is a separate,
authenticated Inbox containing only user-approved text, limited diagnostics, and
screenshots; a related machine is context, not the feedback owner or transport.

## Filesystem and workspace model

Directory access is based on the permissions of the machine account running `pcrd`.
There is no PCR directory allowlist:

1. Android requests filesystem roots or an explicit absolute path.
2. `pcrd` resolves the path and lets operating-system permissions accept or reject it.
3. Android pages and renders directory entries.
4. Selecting a directory registers it as a workspace bookmark and may immediately
   start a run.
5. The registry keeps multiple workspaces, the latest run, and resume context.

Registration is for navigation, not authorization. A user can browse or start from
a new path without registering it first. Removing a registry item never removes the
directory.

## Runtime ownership

`pcrd` is a long-lived process and the only remote authority. It imports one PCR
version at launch and owns one `PluginRunManager`, preserving PCR's existing
persistent Worker behavior. Updating or committing PCR source cannot hot-reload an
already-running daemon.

Native clients use the relay and never become owners of a daemon process. Local
diagnostic tools can use the authenticated Unix socket. The TUI and plugin runtime
both execute through PCR core, and all frontends use the same ordered candidate
finalization coordinator for sync, session promotion, persistence, and cleanup.
`pcrd` uses the plugin runtime as its execution adapter.

## Protocol rules

The current wire protocol is version 1. Every request includes:

- `protocol_version`
- `request_id` for request/response correlation
- `command_id` for durable mutation deduplication
- `client_id`
- namespaced `action`
- JSON `body`
- optional `expected_revision` for optimistic workspace-registry updates

User-facing state is modeled separately from technical worker runs:

```text
Machine -> Workspace -> Conversation -> Turn -> Agent Candidate
```

`Run` remains the execution record used by workers, logs, cursors, and recovery. A
Conversation owns ordered Turns; each Turn points to its execution `run_id` and owns
its candidate identities. Continuation records the exact source Turn and Agent
Candidate, so viewing another Agent cannot silently change history. The canonical
schema is `remote/protocol/pcr-conversation-v1.schema.json`; one generator emits the
Python, Kotlin, and Swift types checked by CI. Native clients call `turns.start`,
`turns.get`, `turns.continue`, and `turns.finalize`. The former `runs.*` lifecycle
names remain temporary transport aliases for older clients.

Read operations use cursor pagination. `runs.list` uses an opaque cursor derived
from the immutable `(created_at, run_id)` ordering key, so inserting or deleting a
newer run cannot shift the next page. Run and event pages are bounded by both item
count and serialized byte size; clients should continue with `next_cursor` while
`has_more` is true rather than assuming a fixed 100-run history window. Mutation
commands are allowlisted by the Mac controller and journaled before execution. A
completed command is replayed from its stored result. If the daemon dies after
journaling but before recording completion, the command becomes
`command_in_doubt`; PCR does not silently execute it twice.

Native clients consume a versioned transcript Item stream from `runs.agent` rather
than interpreting Codex JSON independently. Each Item has a stable event ID, kind,
text, and optional operation/status fields. Raw events remain as a compatibility
fallback. Long Agent lines are split into bounded display events before they enter
the remote event log. The saved final response has an independent UTF-8 byte cursor,
so clients can reconstruct it exactly without placing a multi-megabyte string in one
encrypted response. Async client responses are applied only while their Mac,
workspace, run, Agent, and request context is still current.

`workspaces.settings.get` and `workspaces.settings.update` expose the same
per-workspace configuration store used by the TUI. Updates are partial and atomic,
so a phone changing the Agent count does not reset model, effort, fast mode, or
workspace behavior selected on the Mac.

`settings.defaults.get` and `settings.defaults.update` manage the conversation
profile owned by the target machine daemon. A client addresses an explicit machine
when it reads or changes this profile, so paired machines keep independent defaults. The daemon
copies its profile into a directory only when that directory is first registered as
a workspace; later changes do not overwrite existing workspace settings or carry a
resume session between paths.

The relay preserves these envelopes inside end-to-end encryption and adds a
monotonic per-device transport sequence, acknowledgements, expiry, and replay
windows. Transport sequence and PCR event cursor are intentionally separate.

On macOS, the long-lived `pcrd` process records runs it has observed in progress
and delivers the single all-Agents-terminal notification itself. The transition
state is persisted across daemon restarts, so the SwiftUI app does not need to be
open or polling. The app keeps its notification fallback only for remote machines
that do not advertise daemon-owned completion notifications.

## Server responsibilities

The relay service provides:

- short-lived one-time QR authentication for Android devices;
- short-lived access tokens and rotating refresh tokens;
- QR-based Mac/Android device pairing;
- online-device routing over WebSocket;
- encrypted offline command envelopes with bounded retention;
- device and token revocation without storing prompts or source text;
- a private signed-Android-release catalog and device-bound one-use downloads;
- a private product-level feedback Inbox with idempotent Outbox delivery, attachment
  validation, account isolation, status history, and developer administration.

PostgreSQL is the initial durable store. A single relay process is sufficient for
the first release; Redis is deferred until horizontal fan-out is actually needed.

## Android update trust model

The update route is not treated as a secret. Knowing the fixed route grants no
access. The protocol is:

1. Android authenticates with its normal bearer token and names one pinned, active
   Mac from the same paired account.
2. The relay returns only metadata for a newer non-revoked release.
3. When the user chooses to update, the relay issues a random five-minute ticket
   bound to the user, Android device, Mac, release ID, and APK digest. Only the
   ticket digest is persisted.
4. Android sends bearer authentication and the ticket in
   `X-PCR-Update-Ticket` to a fixed POST endpoint. The relay atomically consumes the
   ticket before streaming one private artifact.
5. Android independently checks the byte count, SHA-256 digest, package, exact
   newer version code, the published signer fingerprint when configured, and the
   installed signing lineage. It then stages the APK with `PackageInstaller`, which
   requires system confirmation.

The artifact directory is owner-only and outside every Caddy or FRP web root.
There is no public file server and no remote upload API. Release publishing and
revocation are local relay-administration operations. A Mac need not be online for
an already paired phone to update, but revoking either device prevents ticket use.

## Native clients

The macOS app is a SwiftUI/AppKit client. Like Android, its target design lists and
controls multiple relay-connected macOS or Linux machines. It is not a daemon host
and closing or uninstalling it must never stop `pcrd` or an active run. `pcrd`
installs independently as a LaunchAgent on macOS or a systemd user service on Linux.

The Android app uses Kotlin, Jetpack Compose, Keystore-backed storage, and an OkHttp
WebSocket. It supports multiple machines, multiple workspaces per machine, arbitrary
remote path navigation, PCR run launch and continuation, resumed conversation history,
live Agent transcripts, Agent status, kill, and finalize. Run state and polling are
keyed by machine, workspace, and run, so switching the visible workspace does not stop
another workspace's updates. WorkManager follows active runs in the background.

Workspace document links use the same end-to-end encrypted controller channel as run
events. `pcrd` resolves each request against the registered original workspace or the
explicitly selected Agent copy, rejects traversal and escaping symlinks, and returns
bounded pages with a stable file identity and per-page digest. The Relay only forwards
ciphertext; it never hosts a source-file URL. Android stores the completed file in
app-private cache, renders small Markdown documents natively, and grants other apps
temporary read-only access through `FileProvider`. The macOS client uses the same
protocol, renders small Markdown documents in-app, and opens other cached files with
the user's registered macOS application.

Client code is split by responsibility: shared TUI formatting, components, command
handling, and rendering live in separate modules. Android and macOS each have machine,
workspace, conversation, Turn, and Agent Candidate repositories populated from the
generated protocol. Their ViewModels coordinate requests and publish UI projections;
they no longer own the durable domain maps directly. Browser, pairing, update, and
notification flows remain in focused modules.

macOS development builds use one persistent local signing identity rather than a new
ad-hoc identity on every build. The build creates that identity in an app-specific
build-only keychain when necessary. The application itself contains no Keychain or
LocalAuthentication calls. Its non-secret window, selection, draft, and scroll state is
stored in an owner-only application file; Relay keys and tokens belong to `pcrd` in its
owner-only service state. Release verification checks the designated requirement across
two builds, installs both versions in sequence, and launches each installed build twice.
This preserves the code identity used by macOS privacy permissions across local upgrades;
public distribution still requires Developer ID signing and notarization.

## Delivery sequence

1. Done: versioned protocol, filesystem browser, registry, `pcrd`, and contract tests.
2. Done: relay, PostgreSQL migration, QR authentication, pairing, E2EE, reconnect, and
   offline replay.
3. Done: macOS development manager and Android foreground client.
4. Done: private authenticated Android release checks, downloads, and system install.
5. Done: independent macOS LaunchAgent and Linux systemd user-service installers.
6. Done: the local `pcrd` authenticates as an end-to-end encrypted Relay controller,
   keeps one persistent controller connection, discovers trusted macOS/Linux machines,
   and forwards the macOS app's local Unix-socket requests to the selected machine. Relay
   keys and tokens never enter the app process or its saved UI state.
7. Done: Android and macOS submit feedback directly to the Relay product Inbox; a
   target machine is optional diagnostic context and does not need to be online.
8. Done: daemon-owned macOS completion notifications, Android background tracking,
   stable run/event pagination, and a unified internal `release/` bundle.
9. Done: persistent local macOS signing plus repeat-launch and upgrade verification.
10. Done: Android Agent diff review with byte-paged full patches.
11. Done: shared Conversation/Turn/Agent Candidate protocol, generated cross-language
    types, five-level native state repositories, and unified finalization.
12. Next: signed public machine packages, failure injection, and release security
    review.
