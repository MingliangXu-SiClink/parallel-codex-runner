# Codex Proxy And External File Links Acceptance

Verified on 2026-10-02. Evidence directory:
`/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/NetworkLinks-20261002`.

## Root Causes And Changes

- The global Codex configuration contained an unsupported root-level `stream_max_retries`. Removed only that setting, preserved all other settings, and retained a backup. Codex 0.159.2 configuration reads no longer report the ignored setting.
- Native file links were unnecessarily sent through run-bound workspace resolution. Explicit local file and directory links now use native metadata and preview handling, including paths outside the selected workspace. They do not require a live daemon or matching run.
- Local Markdown relative links resolve against the opened document's directory. Line references, Unicode paths, file URLs, and ordinary symlinks are supported. Missing files produce a localized error without removing the previous preview. Non-file special paths are rejected.
- This change applies to the local machine only. Relay paths are not interpreted as local paths, and remote filesystem access controls remain intact.
- Codex process environment construction now inherits macOS system proxies when no explicit proxy override exists. SOCKS proxy schemes are normalized, and localhost bypasses are retained. Explicit overrides, including empty values, take precedence.
- Added a Codex-only shell wrapper using the requested Clash endpoints. Other shell commands and Relay traffic are not globally rerouted.

## Verification

- Python source regression suite: 317 tests passed.
- Installed pcrd 0.1.66 artifact verification: 20 tests passed.
- Full macOS suite: 249 tests in 34 suites passed; protocol and desktop-state contracts passed.
- Icon slot, ICNS, and native rendering checks passed. The signed client was built with the macOS 26.5 SDK.
- Official Codex app-server configuration and model-list calls succeeded with the constructed environment; GPT-6.1 Sol was present. No inference request was made for this probe.
- Before the changes, all five active native Codex processes already had established connections through Clash port 7890. Missing proxy use is therefore not established as the cause of their reconnect messages. HTTP and SOCKS TLS connectivity checks both reached the ChatGPT service and returned the expected unauthenticated 401 response.

## Real Native Client Walkthrough

The release binary was exercised in a separate, isolated native client:

- Opened an external Markdown file and followed its relative text-file link.
- Opened an absolute text-file link with a line reference.
- Opened an external directory in Finder.
- Confirmed a missing-file error retains the existing preview.
- Stopped the isolated test daemon and opened the external Markdown file again successfully.
- Confirmed no external-file workspace-resolution RPCs were issued.

Screenshots: `outside-markdown.png`, `outside-relative-text.png`, `missing-retains-preview.png`, and `outside-folder.png` in the evidence directory. The isolated app and daemon were stopped, and temporary authentication data was removed.

## Publication And Deployment

- macOS 0.1.394 / build 394 was published to the private update catalog, installed at `/Applications/Parallel Codex.app`, and restarted with explicit user approval. Its installed binary matches the tested release and passes strict signature verification.
- The formal client restored the active conversation and displayed the running Agents and GPT-6.1 Sol. The formal external-file click was not repeated after the user changed the interface; the isolated native walkthrough above is the file-opening acceptance evidence.
- All 55 observed background Python, Node, and Codex processes were preserved across the client installation. The active persistent worker PID remained 43273.
- pcrd 0.1.66 was built and installed into its separate immutable runtime. At the initial client-deployment verification, the running daemon remained healthy on 0.1.65 and the user's task was still active; its LaunchAgent was not changed at that time.
- A thread heartbeat named "任务结束后启用 pcrd 0.1.66" deferred activation while any run or operation remained active. Its activation script refuses to restart while runs or operations are active and verifies health, pairing, and model availability after activation. The completed activation is recorded below.
- Android and Relay versions and artifacts were not changed.

## Deferred Daemon Activation

- The 2026-10-02 17:45 Asia/Shanghai follow-up enabled pcrd 0.1.66 after repeated idle checks confirmed no active runs, including synthesis Agents, and no active operation. Only the scheduler LaunchAgent was restarted. No task was adopted, discarded, stopped, or retried.
- An independent IPC verification at `2026-10-02T09:47:46.481097+00:00` confirmed `system.capabilities.daemon_version = 0.1.66`, `system.health.pcr.ok = true`, `active_runs = []`, `active_operation = null`, and `detached_workers = true`.
- `codex.model_options` confirmed GPT-6.1 Sol and the `max` reasoning effort remain available. `controller.status.paired = true`; existing pairing was retained.
- The activation script verified that previously observed Node service process identities were preserved. The formal macOS client was not restarted: its existing PID 82356 and start time `2026-10-02 14:05:04` remained unchanged. The client had separately been updated to 0.1.396 before this follow-up.
- Newly launched Codex processes use the configured Clash HTTP/HTTPS proxy at `127.0.0.1:7890`, SOCKS proxy at `127.0.0.1:7891`, and localhost bypass. No running task's private configuration or research directory was changed.
- Activation evidence: `deployment-0.1.66.json` in the evidence directory. The completed deployment follow-up is paused after verification.

## Limits

- The proxy/configuration changes apply to newly launched Codex processes; the active pinned worker was not replaced or edited.
- Reconnection failures were not reproduced to a confirmed provider or network root cause. Do not claim that configuring a proxy eliminates every reconnect failure.
- ChatGPT Web consultation was unavailable under the account's temporary feature restriction. Local code, official configuration evidence, tests, and native client operation were used; no completed Web consultation is claimed.

Deployment evidence: `client-installation.json`, `final-verification.json`, `network-verification.json`, `relay-publish-394.json`, `relay-catalog-394.json`, and `deployment-0.1.66.json`. Client-deployment evidence refers to the original 0.1.394 release; the daemon activation above did not reinstall or restart the later client.
