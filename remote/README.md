# PCR Remote

[简体中文](README.zh-CN.md)

PCR Remote extends Parallel Codex Runner across independent machine and client
components without moving project source code to the relay server:

- **Machine endpoint:** `pcrd` runs on macOS or Linux and owns PCR runs,
  filesystem browsing, Codex processes, workspace copies, and sync-back.
- **Relay server:** authenticates users and devices, then relays encrypted command
  and response envelopes.
- **Android and macOS apps:** select a connected machine, browse its directories,
  manage multiple workspaces, start or continue runs, watch ordered Agent
  transcripts, and explicitly choose an Agent to finalize.

The current implementation includes the cross-platform machine control plane,
native macOS app, Android client, and relay service. The user-facing clients live in the
top-level [`apps/`](../apps/) directory; this directory contains only the shared
remote protocol, relay service, and deployment files. It provides a versioned protocol,
unrestricted directory browsing under the logged-in machine user's real permissions,
a persistent multi-workspace registry and shared run settings, typed transcript
Items, at-most-once run commands, one-time QR device pairing, end-to-end encrypted
envelopes, and offline relay delivery.

On macOS, the long-lived `pcrd` service also owns completion notifications. Once
every Agent reaches a terminal state, including runs with failed or stopped
Agents, it posts the workspace name even when the SwiftUI app is closed. Pending
terminal transitions survive daemon restarts and failed deliveries are retried.

## Per-Agent questions

With `pcrd` 0.1.95, macOS 0.1.438 and Codex app-server 0.159.2 or later,
new native Agent executions can present structured questions inside their own
conversation. Each answer returns to the original native request, not a new
prompt. Other Agents keep running; a nonblocking question does not pause its
own Agent either. Existing executions are not migrated or restarted.
Only runs created by a question-capable client advertise the native question tool.
The additive `client_capabilities: ["agent_questions"]` transport metadata is
forwarded over encrypted Relay and recorded in the run's ownership marker. It
does not change the original prompt body, command ID or command fingerprint.
Older clients and standalone CLI runs retain their non-interactive behavior.

`runs.agent` includes `input_requests`; `runs.question` reads one original
receipt. `runs.answer_question` binds an immutable explicit answer to its run,
Agent, request ID and native connection ID. Clients persist the choice before
delivery and reconcile the original receipt before retrying. Server command
deduplication remains in force. No answer is selected automatically.

Client disconnection or an IPC restart does not expire a still-live native
request. Native owner loss does: saved choices remain available, but are never
replayed to a new process or request. Local persistence, server acceptance and
native reception are distinct facts; an uncertain native delivery is not
reported as successful adoption. Permissions and result finalization remain
separate from questions. Older clients ignore the additive fields, and newer
clients retain normal reading and task controls when connected to older daemons.

## Local development

Install the current checkout, start the daemon, then query it from another shell:

```bash
python3 -m pip install -e .
pcrd serve
```

```bash
pcrd status
pcrd call filesystem.roots
pcrd call filesystem.list --body '{"path":"~","directories_only":true}'
pcrd call workspaces.add --body '{"path":"~/Desktop/my-project"}'
```

Provision a development machine once so a paired client can discover it without
asking the user for server details:

```bash
export PCR_RELAY_MACHINE_ENROLLMENT_TOKEN="..."
pcrd configure-server --url https://pcr.example.com --name "Studio Computer"
```

After provisioning, show a one-time QR code directly in the machine terminal and
scan it from the Android app:

```bash
pcrd pair-device
```

The command waits for mutual verification and prints the pairing result. Linux
does not require the macOS app. The legacy
`PCR_RELAY_MAC_ENROLLMENT_TOKEN` variable remains accepted for compatibility.

The release bundle's `pcrd/install-pcrd.sh` installs each version in a dedicated
virtual environment, not the user's shared Python environment. On macOS it is
under `~/Library/Application Support/parallel-codex-runner/runtime/<version>`;
on Linux it is under `${XDG_DATA_HOME:-~/.local/share}/parallel-codex-runner/runtime/<version>`.
Use that environment's `bin/pcrd` for service maintenance. `PCR_RUNTIME_ROOT`
can override the runtime root when using the installer.

After installing a newer wheel, run that environment's `pcrd install-service` again. It refreshes
the service definition and restarts the machine endpoint, so Linux does not keep
running Python modules loaded from the previous installation.

Service maintenance is independent from the macOS app:

```bash
pcrd service-status
pcrd service-logs --lines 200
pcrd restart-service
pcrd uninstall-service
```
`pcrd relay-enroll`, `pcrd relay-poll`, and `pcrd relay-status` remain available for
protocol development and diagnostics.

Install `pcrd` as an independent per-user service. The same command creates a
LaunchAgent on macOS or a systemd user service on Linux:

```bash
pcrd install-service \
  --server-url https://pcr.example.com
# later: pcrd uninstall-service
```

The daemon socket, authentication token, command journal, and workspace registry
are stored below the current user's application state directory with owner-only
permissions. The token authenticates local IPC only; internet transport uses
separate device pairing and end-to-end encrypted envelopes.

## Workspace access

PCR Remote intentionally does **not** require a folder to be pre-authorized on the
machine. A paired client may browse every directory that the `pcrd` user can access,
including mounted volumes, and register any directory as a managed workspace.
Normal operating-system permissions remain authoritative. On macOS, protected
locations may additionally require Full Disk Access for the installed Python or
`pcrd` executable.

Removing a workspace from the Android app removes only its PCR bookmark. It never
deletes the original directory.

## Private Android update channel

A phone with an active pairing can check for and install new Android releases from
the relay. The APK is not placed under a public download directory. The relay uses
bearer authentication plus a short-lived, one-use ticket bound to the Android
device, one active machine endpoint on the same account, the release, and its
SHA-256 digest. The ticket travels in a header rather than a URL. The machine may be offline; revoking
either device blocks new downloads.

The Android app verifies the complete APK, package, newer version, and signing
lineage, then delegates to Android's user-confirmed system installer. Relay setup
and publishing commands are documented in [`server/README.md`](server/README.md).

## Project layout

```text
apps/
  macos/          Native macOS client
  android/        Kotlin/Compose multi-machine and multi-Workspace client

remote/
  protocol/       Wire contract shared by all three clients
  server/         Device identity and opaque WebSocket relay service
  infra/          FRP, TLS, PostgreSQL, and systemd deployment templates

src/parallel_codex_runner_core/remote/
  protocol.py             Envelope validation and response format
  filesystem.py           Machine-user-scoped filesystem browser
  workspace_registry.py   Multiple workspace persistence
  journal.py              Durable at-most-once command protection
  controller.py           Allowlisted PCR command dispatcher
  ipc.py                  Authenticated Unix socket transport
  daemon.py               pcrd CLI and service entry point
  crypto.py               X25519 and AES-GCM envelope protection
  relay_config.py         owner-only server provisioning for one-click pairing
  relay_http.py           enrollment, refresh, and device discovery
  relay_bridge.py         encrypted WebSocket bridge owned by pcrd
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete target design and
[SECURITY.md](SECURITY.md) for the consequences of remote filesystem access.
