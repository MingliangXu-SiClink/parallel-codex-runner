# ASUS WSL relay deployment

This directory contains deployment templates for the relay host named
`asus_win_wsl`. They deliberately contain no real domain, FRP token, database
password or enrollment token.

## Traffic path

```text
Android / Mac
      |
  HTTPS + WSS
      |
public frps TCP 443
      |
existing frpc tunnel
      |
Caddy 127.0.0.1:8443
      |
pcr-relay 127.0.0.1:8765
      |
PostgreSQL
```

FRP is transport, not TLS termination. The Android and Mac clients must see a
valid certificate for the relay hostname. Forward public TCP ports 80 and 443 to
Caddy if Caddy performs ACME. When the public side already terminates TLS, replace
the Caddy portion with that reverse proxy and keep the `X-Forwarded-*` headers.

## Install

1. Create a dedicated account and data directories:

   ```bash
   sudo useradd --system --home /var/lib/pcr-relay --shell /usr/sbin/nologin pcr-relay
   sudo install -d -o pcr-relay -g pcr-relay -m 0750 /var/lib/pcr-relay /var/log/pcr-relay
   sudo install -d -o pcr-relay -g pcr-relay -m 0700 \
     /var/lib/pcr-relay/android-releases \
     /var/lib/pcr-relay/macos-releases
   sudo install -d -o root -g pcr-relay -m 0750 /etc/pcr-relay
   ```

2. Build a wheel on a trusted machine and install it into `/opt/pcr-relay/venv`.
   Do not run the relay from an editable checkout.

3. Create PostgreSQL role/database `pcr_relay`, copy
   `pcr-relay.env.example` to `/etc/pcr-relay/pcr-relay.env`, and replace every
   placeholder. Keep this file mode `0640`, owned by `root:pcr-relay`.

4. Apply schema migrations before starting the service:

   ```bash
   sudo -u pcr-relay sh -lc 'set -a; . /etc/pcr-relay/pcr-relay.env; set +a; /opt/pcr-relay/venv/bin/pcr-relay-migrate'
   ```

5. Install `pcr-relay.service` under `/etc/systemd/system`, then start it:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now pcr-relay.service
   curl --fail http://127.0.0.1:8765/healthz
   ```

6. Configure Caddy and append the FRP proxies from `frpc-pcr.ini.example` to the
   existing `~/auto_start_scripts/frp` configuration. Back up the current FRP
   configuration first. Validate the public HTTPS endpoint before pairing a Mac.

7. Create a fresh pairing in the Mac app and scan its one-time QR code from the
   Android app. The QR expires according to `PCR_RELAY_PAIRING_SECONDS`.

## Publish an Android release

Do not add `file_server` or a public `/downloads` route to Caddy. Transfer a signed
APK through the private administration channel, place it in a temporary path owned
by `pcr-relay`, then run the local admin command:

```bash
sudo -u pcr-relay sh -lc '
  set -a
  . /etc/pcr-relay/pcr-relay.env
  set +a
  /opt/pcr-relay/venv/bin/pcr-relay-admin publish-android \
    /private/staging/Parallel-Codex.apk \
    --version-code 4 --version-name 0.1.3 \
    --release-notes "后台发送与网络恢复能力改进" \
    --signing-cert-sha256 <64-hex-character-digest>
'
```

After publishing, remove the staging copy. The admin command copies the artifact
to a randomized owner-only filename; Android downloads it only through the
authenticated one-use-ticket endpoint.

## Publish a macOS release

Build and sign the app on the trusted Mac, transfer the ZIP through the private
administration channel, and publish it with the exact metadata reported by
`codesign`:

```bash
sudo -u pcr-relay sh -lc '
  set -a
  . /etc/pcr-relay/pcr-relay.env
  set +a
  /opt/pcr-relay/venv/bin/pcr-relay-admin publish-macos \
    /private/staging/Parallel-Codex-macos.zip \
    --build-number 161 --version-name 0.1.161 \
    --release-notes "Private macOS update" \
    --designated-requirement "<codesign designated requirement>" \
    --signing-authority "<codesign signing authority>"
'
```

Remove the staging copy after publishing. The signed archive remains outside the
web root and is available only to a paired macOS controller through a short-lived,
one-use download ticket.

## WSL lifecycle

Use systemd inside WSL and a Windows Scheduled Task that starts the distribution
at login or boot. The task should only wake WSL; systemd owns `pcr-relay`, Caddy,
PostgreSQL, and the existing FRP service. This avoids duplicate ad-hoc background
processes.

Enabling a Linux service is not sufficient to start its WSL distribution after a
Windows update. The Windows owner, distribution name and Linux login user must be
explicit. Do not use `SYSTEM`, a different administrator account, the default
distribution, automatic login, or a stored user password as a shortcut.

`pcr-relay-wsl-guard.xml.example` and `render_wsl_guard.py` prepare a native Task
Scheduler definition. It keeps a single unprivileged `wsl.exe` caller attached to
the original distribution; it never starts a second relay/frpc/database, runs a
migration, or restarts any service. Its boot/logon triggers and five-minute
calendar fallback share `IgnoreNew`; it has no 72-hour execution limit and does
not wake a deliberately sleeping/off host. Disable the guard before intentional
WSL maintenance. Ordinary PCR task stops are unrelated and must remain stopped.

Render only after verifying the original owner's SID, exact distribution,
non-root Linux login user, original machine identity and service configuration:

```bash
python3 render_wsl_guard.py \
  --user-sid '<original Windows user SID>' \
  --distribution '<original distribution>' \
  --linux-user '<original Linux login user>' \
  --start-boundary '2026-10-17T00:00:00' \
  --output '<new private staging path>.xml'
python3 -B -m unittest -v test_wsl_guard.py
```

The rendered definition is UTF-16, compatible with the Unicode string consumed
by Task Scheduler. Before registration, verify its exact SHA-256 on Windows and
use `Schedule.Service` / `RegisterTask` with `TASK_VALIDATE_ONLY` (flag `1`), which
does not register or run it. A successful schema check is not deployment.
Registration needs the original Windows user's administrator session. A denied
registration must not trigger UAC bypass, privilege changes, a new account or a
different logon mode. Do not overwrite an unrelated task or use `-Force`.
Future registration should create only a new, initially disabled task, verify
its exact principal/actions/settings, then separately enable it. Time triggers
can run at registration even without a registration trigger. Audit the original
account's effective batch-logon rights before and after: Task Scheduler can
assign those rights during registration, so the absence of an explicit policy
command does not prove no policy change. Do not silently add rights. Verify the
user's actual ability to disable the task for maintenance; do not change its ACL
as an unannounced workaround.

The S4U principal stores no password but has restricted network/encrypted-file
access. Validate actual execution in that context, original WSL boot ID, service
PID continuity, pairing and the existing authenticated bridge. A manual run while
logged in is not a cold-boot or logged-out test. Do not reboot/log out or stop the
production distribution to create a test during competition.

This guard is a recovery-delay mitigation, not high availability: a powered-off,
disconnected or failed source still cannot serve the original database. See
[Microsoft's WSL lifecycle notes](https://learn.microsoft.com/en-us/windows/wsl/systemd)
and [Task Scheduler logon semantics](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype).

The deployed task is named **PCR Relay WSL Guard**. Its default task ACL is not
broadened: deliberate maintenance uses the original owner's normal administrator
PowerShell session. Disable future triggers before intentional WSL maintenance:

```powershell
Disable-ScheduledTask -TaskName 'PCR Relay WSL Guard'
Get-ScheduledTask -TaskName 'PCR Relay WSL Guard' | Select-Object TaskName, State
```

Disabling triggers is not the same as stopping the existing attached caller or
shutting down WSL. Do not stop either to verify this procedure during competition.
After explicitly requested maintenance, re-enable the same task instead of
registering another one. Task Scheduler normalizes XML by omitting default
values; verify effective COM settings as well as the exported XML. In particular,
an omitted `RunLevel` must still read back as least privilege (`0`). One running
task may contain the native `wsl.exe` launcher/child pair; verify the task instance
identity and single Linux sleep process rather than counting executable names.
Repeated triggers can update the last-request status while the original instance
continues. `0x800710E0` is a generic request refusal, not a success whitelist or
proof that the existing caller failed. Check the live instance, Linux process,
original service identity and authenticated business connection together. Do not
stop a healthy instance or change `IgnoreNew` merely to clear that status.

The 2026-10-17 registration, unchanged user-rights exports, real Session-0 S4U
execution and non-disruptive repeat-run checks are recorded in
[the relay acceptance record](../../qa/2026-10-17-relay-502-recovery.md).
No production cold-boot or logged-out test was performed.

## Recovery order

Keep an SSH route that does not depend on the new relay tunnel. Roll out in this
order: PostgreSQL, migration, relay health check, Caddy local TLS check, FRP test
port, then production public ports. Never overwrite the working FRP configuration
until the test proxy has stayed connected and `/healthz` succeeds externally.
