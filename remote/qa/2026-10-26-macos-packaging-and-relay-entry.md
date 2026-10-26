# macOS Packaging and Relay Entry Recovery

Checked on 2026-10-26, Asia/Shanghai. The user requested packaging of the modified
source and a Relay deployment audit, then explicitly authorized restoring the
existing Nginx entry and fixing certificate renewal. No client installation,
daemon replacement, new task, result adoption or research-file change was made.

## macOS Package

- Source checkout: `/Users/mingliangxu/Desktop/parallel-codex-runner`, HEAD
  `ecc3960f972eeb2b659d729ac0b811f4e3b4ae92`. Preserved the user's three existing
  version edits: macOS 475, pcrd 106 and Relay 21.
- The standard `apps/macos/dist/Parallel Codex.app` was still **0.1.337**. It now
  contains the exact verified **0.1.475 / build 475** archive contents.
- Archive: `release/clients/macos/Parallel-Codex-macos-0.1.475.zip`;
  14,975,853 bytes; SHA-256
  `975d251d10daf9465db1155109964e0edc7085e238b9a7e5dfd5b37f5e07721b`.
- Packaged executable SHA-256:
  `32bafa6adf260b9139fc303b5485a8c25afe2ced9d35d51bf276c7335ae2a4d3`.
- Used the existing encrypted offline `Parallel Codex Release Signing` store.
  Strict deep native signature verification, publisher continuity with the
  installed client, extracted version and packaged math/syntax rendering pass.
  No new identity, Keychain prompt, trust change or verification exception.
- `test-client.sh`: **703 tests in 63 suites pass**, plus protocol, desktop-state
  and icon checks. Interactive foreground suites remain opted out. Signing-store
  tests: 2 pass; disabled-signing regression: 1 passes.
- Built with the installed Command Line Tools and explicit
  `/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`, two build jobs.
  No Xcode GUI or production app launch was used for validation.
- Updated only the macOS component/artifact in the generated local release
  manifest. pcrd/Relay manifest entries still describe their existing artifacts.
- **Packaged, not published or installed.** `/Applications/Parallel Codex.app`
  remains 474. The authenticated server catalog still returns 474. The macOS app
  does not embed pcrd; the existing LaunchAgent still loads independent 105.

## Deployment Findings

1. The original source service is still **pcr-relay 0.1.20**, PID 374, started
   October 22 at 23:35:53. Its installed Python package and all 21 current source
   Python/migration files match byte-for-byte. The source version is 0.1.21, but
   no differing Python implementation was found in that comparison. Do not call
   the service upgraded to 21 or infer missing behavior from the version alone.
2. The database head is `0006_device_login`, matching the source migrations.
   No migration or database mutation was run.
3. Actual FRP configuration maps `pcr-relay-origin`, cloud TCP 39080, to the
   original WSL `127.0.0.1:8765`. Both source and cloud upstream returned 200.
4. The public HTTPS entry failed while **no Nginx process or 443 listener existed**.
   This was not inferred merely from the failed systemd unit. October 22 logs
   show stop/start overlap and a subsequent "already running" start failure;
   the initiating operator is unverified. Do not attribute it to the user's
   Relay upgrade.
5. Renewal repeatedly failed with HTTP-01 **connection refused**. The old
   certificate had not expired when inspected; it would expire October 27 at
   07:12:37 local time. Certificate expiry was not the observed handshake cause.

## Authorized Recovery

- Rechecked absence of Nginx, verified its existing configuration, and started
  the existing service. Master PID 212332 started at 18:35:44. Public HTTPS
  immediately returned 200 with normal TLS verification.
- Ran the existing certificate renewal service, preserving its geographic-filter
  validation window and cleanup hook. It succeeded at 18:36:17. The actual served
  certificate now expires **2026-11-02 09:37:42 Asia/Shanghai**; SHA-256
  `25ae830ee47fb5b6c7268d75de12151a8f6c5f0e12ddc8f5e88028ebe830f2ef`.
- Corrected the renewal dependency: `After=nginx.service` alone does not start
  the web server. The existing drop-in now also has `Requires=nginx.service`.
  No healthy Nginx restart was used. Deployed only after the previous drop-in
  hash matched the checked-in original; reloaded systemd configuration.
- Two dependency/cleanup regression tests pass. Native unit verification passes
  when the existing generated SysV Nginx unit is explicitly included. An initial
  offline verify without that generated unit reported it missing; the live unit
  was loaded, not absent. Subsequent service execution succeeded at 18:39:22.
- The live manager shows the expected drop-in and `Requires`/`After` dependency.
  The active timer targets `pcr-relay-cert-renew.service`; its next trigger is
  **2026-10-27 03:30:17 Asia/Shanghai**. The existing deploy hook uses
  `/usr/bin/nginx -s reload`, not stop/restart. The temporary validation exception
  is absent after completion. No permanent firewall allowance or TLS bypass was
  added. The next automatic firing has not yet been observed.
- PCR vhost SHA-256 remains
  `7524f276fb383f802d6bf5acf47e6798b6596eb1569f82068177712b19b55c96`.
  No PCR/shared route was manually changed. Anonymous `/v1/devices` still returns
  401; `/downloads/` still returns 404.

## Real Connection and Preservation

- Existing pcrd refreshed credentials through its normal mechanism and restored
  its original authenticated WebSocket. No second bridge consumer, manual token
  refresh or production-message replay was created.
- Its link has remained connected since 18:36:13; the old last-error timestamp
  precedes recovery. Source socket PID 374/fd16/peer58038 remained the same while
  sent/received counters advanced from 403/978 to 433/1028 bytes.
- A supported authenticated macOS catalog check through the existing pcrd
  returned the original 474 release and digest, first in 2.58s and then 0.96s.
  No download ticket, install or publication was requested.
- Original daemon PID4374, client PID33713, memory worker PID24260 and original
  native executors PID54203/54204 retained their start times. This is preservation
  evidence, not a claim that every remote calculation was revalidated.
- The separate pcrd105 long-history read congestion is **not fixed by this work**.
  No repeated heavy run-list/health probes or service replacement were used.
- No live stopped-server failure drill, cold boot, or systemd restart-race fix
  was performed. The dependency repair and actual renewal/connection recovery
  are the verified scope; the old shared Nginx init script remains unchanged.

Private evidence and packaging helpers:
`~/Library/Caches/ParallelCodexMaintenance/PackageRelay-20261026/`;
`package.json` records source hashes and exact bytes, `deployment.json` records
current TLS, API, source, schema, process and systemd observations. Independent
Pro discussion reviewed boundaries; local tests and live observations, not its
agreement, establish the results.
