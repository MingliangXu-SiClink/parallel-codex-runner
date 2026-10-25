# Relay 502: Origin Disconnected

## User Requirement

Immediately diagnose and restore the production relay, deploy any necessary verified repair, and prove actual authenticated recovery without disrupting active competition tasks. A scheduled investigation or a superficial health page is not completion.

## Confirmed Fault Boundary

All times below are Asia/Shanghai on 2026-10-17 unless marked UTC.

- Public `https://8.153.193.1/healthz` actually returns **HTTP 502**, including at 00:43 and 00:49. The probe used ordinary TLS validation and explicitly bypassed local HTTP proxy settings. There was no certificate bypass, new trust exception or authentication change.
- The live production Nginx config at `/www/server/panel/vhost/nginx/pcr-relay.conf` proxies the PCR paths to **`http://127.0.0.1:39080`**. Its TLS and shared camera/reader routes were not modified.
- Nginx remains running, and the FRP server remains active at PID **1198**. Other existing FRP proxy routes were observed and not restarted or changed.
- The actual configured upstream port **39080 has no listener**. A direct request from the cloud host itself fails immediately with connection refused. This is not a speculative inference from an Nginx error-page appearance.
- At **00:03:28.999**, the FRP server recorded the same ASUS client disconnect closing both **`pcr-relay-origin` / 39080** and **`ssh_asus` / 55300**, followed by `client exit success`. No subsequent ASUS reconnection was observed in the bounded journal readback.
- The ASUS SSH entry closes before handshake. The existing Hong Kong FRP service has no active alternate ASUS mapping. Bounded local name resolution also failed to locate that host.
- The already-running ToDesk app displays **MingliangXu-ASUS offline**, with a remote-power-on control. This proves an unavailable management path, not whether the machine was intentionally powered off, crashed, suspended or lost networking.

The direct failure chain is therefore **ASUS origin/FRP client unavailable -> expected cloud upstream listener absent -> public Nginx 502**. The cloud TLS endpoint and local competition execution have not been shown faulty. The underlying reason for the ASUS disappearance still requires its host logs after access is restored; older hardware incidents are not proof of the cause of this incident.

The FRP cleanup timestamp is the cloud's observation, not a proven Windows shutdown time or the exact start of lost connectivity. `client exit success` is not proof of a clean Windows/frpc shutdown. The failed 55300 SSH attempt depends on the same absent FRP session and is not an independent measurement of the origin's sshd. Existing other proxies do not establish that ASUS can perform a new authenticated FRP login; new-login/error records must also be checked without filtering only the old session ID.

## Safe Recovery Boundary

The deployment records place the relay and PostgreSQL behind this origin; the current database location/content cannot be independently reverified while that host is unavailable. The documented relay application/data paths are absent from the cloud host, and its live PCR-related units contain the certificate-renewal unit but no relay application unit. The other observed Python services were identified as Horizon and the camera API, not substituted as a relay. No verified failover instance or current original database copy has been established. Starting an empty replacement relay/database or changing pairing credentials would not preserve the existing devices, encrypted pending messages or original identity. No such replacement, database restore or migration was attempted.

A targeted question was sent about only waking MingliangXu-ASUS and restoring its WSL relay. Within the user's immediate relay-restoration authorization, the existing ToDesk **remote power-on** action was subsequently tried exactly once, without issuing a restart or shutdown. Its dialog confirmed only that the power-on request was sent, not that the device had booted. A later actual screenshot still showed offline; the SSH route and public 502 remained unchanged. No repeat power-on, remote control session, Windows login, WSL shutdown, host reboot, broad FRP restart, certificate change or service deployment was performed. Foreground inspection after the attempt showed the user's Microsoft Edge, not a raised ToDesk/PCR window.

After regaining access, first inspect actual Windows/WSL shutdown/startup, relay/PostgreSQL/frpc service and ownership records; repair the proven lifecycle failure narrowly. Recovery acceptance must include the original configured source, public TLS HTTP, existing device authentication/pairing and the existing pcrd bridge's normal long-poll or heartbeat cycle. Do not start a second consumer using a real device credential or replay a user prompt merely to test connectivity. A public 200 alone is insufficient.

## Competition Preservation

Before intervention, readback confirmed **4 active task groups**, **8 persistent live worker owners**, pcrd **0.1.98/PID 48685** and macOS **0.1.439/PID 64075**. Active Agent state was 25 running / 10 success, with genuine tools, thinking and intentional waiting; no failed active Agent required recovery. The old outbox is empty; the separate new queued user message remains exactly once.

The cloud investigation did not stop, restart, adopt, retry, resubmit or edit any competition task or its research files. Original process start times were rechecked. No app/pcrd update was substituted for fixing the origin outage.

The 00:43 baseline was independently compared with the 00:55 snapshot: all eight owner PID/start-time records, the loaded client process, receipt-store identity and the original queued item's identity/body hash/destination remained equal; the outbox remained empty and all four active owners were still live without a reported run error. Natural Agent progress was not required to freeze.

## Evidence And Current Status

- Private bounded command evidence: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/Relay502-20261017/diagnosis-20261016T164947.160820Z.json`.
- Actual offline display: `asus-offline.png` in the same private directory; `wake-requested.png` preserves the one power-on-request dialog. That dialog is not recovery acceptance.
- Near 01:00 local time, `asus-after-wake.png` still showed the same device offline. Public TLS `/healthz` still returned 502 at 00:59:27. This is a real source-access blocker, not a task waiting in a repair queue.
- Live task baseline: `/Users/mingliangxu/Library/Caches/ParallelCodexMaintenance/CompetitionWatch/snapshot-20261016T164342.194856Z.json`.
- Post-wake competition verification: `snapshot-20261016T165538.353337Z.json` in that same CompetitionWatch directory.
- Independent Pro discussion reviewed transport/business-plane separation, shared FRP/WSL stop scope, renewal response ambiguity and validation through the existing authenticated connection. Its advice does not authorize power operations or prove recovery.
- The subsequent discussion incorporated the actual absent upstream, simultaneous mapping removal and failed wake result. It reinforced that the cloud's FRP cleanup is not a Windows shutdown diagnosis, old ARP/lease records are insufficient, no verified independent control/state-failover route is available, and source availability cannot be manufactured by changing the cloud proxy. `web-review-followup.png` preserves that discussion; actual machine evidence remains authoritative.

**Status at this checkpoint: diagnosed upstream-origin disconnection; one remote-wake request did not establish host access. Source-host power/network/access is the concrete blocker. Not repaired, not redeployed and not validated recovered.** Append actual host findings and completed deployment/recovery evidence rather than replacing this historical failure record.

## Actual Source Return And Root Cause

After the user said they would power on the host, the original SSH route returned
`MingliangASUS`. No additional power, reboot, WSL shutdown or service restart was
issued by this repair.

- Windows event 1074 at **00:02:26.577** identifies `MoNotificationUx.exe` initiating
  a planned operating-system/service-pack restart. The original WSL journal then
  records an orderly service shutdown and poweroff at **00:02:29-30**. These source
  records explain the subsequent FRP cleanup at 00:03:28; this is no longer a
  guess based on `client exit success`.
- Windows booted at **00:04:38** and `TrustedInstaller.exe` requested another
  planned upgrade restart at **00:05:11**; a subsequent boot is recorded at
  **00:06:03**. The WSL distribution did not have another boot until **01:08:08**.
- A separate user Start-menu power-off request is recorded at **00:43:44**,
  followed by low-power entry at 00:43:53 and return at 01:03:16. Do not attribute
  all of this time to a single boot-trigger failure or claim which wake request
  caused the return.
- The live original `wsl.exe --distribution-id` caller was created at
  **01:08:05.539** by **WindowsTerminal.exe**, with the original distribution GUID.
  It is not evidence of an effective background boot guard. Bounded matching
  scheduled-task/startup reads found no WSL/FRP/PCR startup entry. Task Scheduler's
  Operational history is disabled, so absence of historical trigger events is
  not used as proof that no other launcher ever attempted to run.

The concrete outage was **Windows upgrade restart -> original WSL stopped ->
original FRP upstream removed -> Nginx 502**. The prolonged recovery additionally
exposed missing verified Windows-side lifecycle supervision. The Linux service
startup itself works; a pcrd/client version bump or disabling security updates
would not fix this boundary.

Private evidence: `windows-lifecycle-20261016T170944.597392Z.json` (power events),
`windows-lifecycle-20261016T171219.463019Z.json` (matching startup entries), and
`windows-lifecycle-20261016T172129.432876Z.json` (actual launcher/history state).

## Authenticated Production Recovery

The original WSL boot automatically started the existing services, without a new
application deployment or database migration:

- Original PostgreSQL **18/main**, data directory `/var/lib/postgresql/18/main`,
  PID **259**, start **01:08:09**; the `pcr_relay` database retains devices created
  in August. A read-only, statement-time-bounded SQL query found no live pending
  envelopes. No database, device, token or pairing was recreated.
- Original `frpc-shanghai.service` PID **280**, start **01:08:09**; the cloud now
  observes ASUS control **8f8e3daff235947d**. Other shared FRP routes and the cloud
  frps PID **1198** were preserved.
- Original relay **0.1.20**, PID **440**, start **01:08:11**, `NRestarts=0`.
- Source `8765`, actual cloud upstream `39080`, and normal public TLS `/healthz`
  all return **200**. Anonymous public `/v1/devices` still returns **401**.
- Existing pcrd **0.1.98**, without restart or a second bridge consumer, renewed
  its credentials normally at **01:08:21**, read the protected device list at
  **01:08:22**, and established the original authenticated `/v1/relay` WebSocket
  at **01:08:23**. Subsequent successful device-list requests continue across
  the real 60-second refresh interval. No manual refresh or production prompt
  was submitted as a test.
- At **01:23:49**, link state remains `connected` with the same `since` value.
  The `last_error=HTTP 502` timestamp is **01:07:50**, before recovery, not a new
  failure. The source's same persistent socket `127.0.0.1:8765 <- :50970`, relay
  fd **16**, remained established while sent/received bytes advanced from
  **727/1524** to **829/1694**. This separately supports sustained bidirectional
  liveness of the existing connection; periodic HTTP reads alone were not
  misrepresented as WebSocket heartbeat evidence.

Saved combined recovery evidence:
`diagnosis-20261016T172057.307949Z.json`. No claim is made that a new encrypted user
command was exercised, and none was needed for this recovery.

## Lifecycle Repair: Prepared, Registration Blocked

Prepared a minimal native Windows task definition, not another relay manager:
original Windows SID, explicit **Ubuntu-26.04**, original Linux login user
**mingliang**, S4U without a saved password, least privilege, a single attached
`sleep infinity` caller, boot/logon and five-minute fallback triggers,
`IgnoreNew`, no 72-hour timeout, and no host wake action. It does not issue
service starts/restarts, WSL shutdown, database initialization, re-pairing or
competition commands. Disable it before deliberate WSL maintenance.

The first bounded same-user S4U task-registration attempt was rejected by Windows
with **0x80070005 / Access is denied** before any task ran. The current interop
token is not elevated. No permission/security-policy change or alternate account
was attempted. Readback confirms **`PCR Relay WSL Guard` does not exist**.
The user has been asked for one administrator-PowerShell registration step.

The candidate was corrected during offline/native validation: Task Scheduler
rejects an explicit UTF-8 declaration when passed as a Unicode BSTR; the renderer
now produces UTF-16. A `nobody` preflight completed but emitted user-session/home
warnings, so the final definition uses the verified original regular login user
and explicit `/` working directory instead. Neither preliminary candidate was
registered or used as a production guard.

Final prepared definition:

```text
C:\Users\Mingliang\AppData\Local\ParallelCodexMaintenance\Relay502-20261017\pcr-relay-wsl-guard-v2.xml
SHA-256 f9dc445dc2f68ad47046a0d2f3ff953850a69050530fda73b9980ff3525520c3
```

**7 local policy/renderer tests pass.** Windows' native `TASK_VALIDATE_ONLY`
accepts these exact staged bytes; the explicit distribution's machine identity
matches the original source, and that final check emits no WSL user/home warning.
Evidence: `windows-lifecycle-20261016T172236.153287Z.json`.
This is **prepared and schema-validated, not registered, running, or cold-boot
accepted**. S4U execution and a future logged-out/cold-start boundary remain
unverified. Do not force a production reboot/logout to manufacture that test.

## Post-Recovery Competition Preservation

Snapshot `snapshot-20261016T172349.981938Z.json` was compared with the original
00:43 baseline. All eight owner PID/start-time records, the actual loaded client
process, receipt-store identity and queued message's identity/body hash/target
are unchanged. Original outbox and accepted-handoff are empty; no additional
uninspected window is open. pcrd **48685/0.1.98**, client **64075/0.1.439**, four
active competition groups and their real native execution remain healthy. No
stop/adoption/retry/resend/extra continuation was caused by relay restoration.

The independent Pro follow-up distinguished original service recovery from boot
supervision and permanent high availability. Its screenshot is
`web-recovery-review.png`; agreement is not deployment evidence.

**Current result: original authenticated relay is restored and observed stable
for over 15 minutes; competition execution and message identity are preserved.
Windows startup hardening is blocked on the real administrator registration
boundary, not falsely reported as deployed. The ASUS power/network/database
single point is not eliminated.** The hourly competition monitor remains active
and must not repeatedly retry the same denied registration.

## Latest Human Clarification

The user replied that WSL should now already be started and the present problem
should be gone. That is correct for **current availability**: an additional
ordinary TLS probe at **01:32:20** returns 200 from the original production relay.
The earlier administrator question concerned recovery after a future Windows
restart, not a claim that WSL was still down. The response is not authorization
to register a Windows task or change privileges. No further registration,
elevation or restart is performed; maintain the current healthy service and the
existing hourly competition monitor without repeatedly requesting that step.

The final independent review additionally identified future deployment gates:
create a new task initially disabled (calendar triggers can fire during
registration), read back exact identity/ACL/control settings, and audit effective
batch-logon rights because Task Scheduler may assign rights during registration.
The current denied attempt did not reach that stage. A future approved install
must not silently add rights or override an unrelated task. A five-minute guard
is an intentional keep-running contract: disable it before user-requested WSL
maintenance, and verify the user's actual ability to do so. Schema validation is
not proof of any of these runtime or permission gates.

No startup task has been deployed. The current original relay's actual recovery
does not depend on installing this prepared candidate.

The **01:33:33** snapshot still reports the same connected-since **01:08:23**,
healthy pcrd98, loaded client439 and the original queued item's unchanged
identity/body/target. Normal business progressed during the readback: original
`pcr-20261016T045048Z-0fde9458` is now `finalized` with Agent 11 and all eleven
Agents successful, no error and its old worker retired; a new active run
`pcr-20261016T173222Z-a431ba9d` has worker **90993** and all eight Agents subsequently
read back as running without errors. The other seven original owner identities
and the loaded client remain. No finalization/start/retry/stop was issued by
this relay investigation. This is not a requirement to freeze normal user
progress or resurrect the retired owner. The hourly monitor remains **ACTIVE**;
its saved prompt was updated to current recovery and the user's clarification,
without changing its interval or re-enabling old update jobs.

Final independent control-boundary review screenshot:
`web-guard-boundary-review.png` in the private relay evidence directory.

## Authorized ToDesk Deployment, 01:38-01:54

The latest human instruction explicitly authorized performing the Windows-side
steps through ToDesk. This supersedes the earlier lack of authorization, not the
earlier evidence of current service recovery. ToDesk connected to the original
ASUS, verified the original Windows account/SID in its terminal, and displayed
the unlocked original desktop. No password, security policy or remote-access
permission was changed. The terminal identity screenshot is
`todesk-terminal-identity.png` in the private evidence directory.

Live identity checks distinguish administrator membership from elevation: the
original owner is already a local administrator, but the SSH/interop token is
filtered. A normal Windows `RunAs` process under that same owner completed the
administrator preflight. Execution policy remains `Restricted`; no PS1 file,
execution-policy override, UAC bypass or alternate account was used. Subsequent
native scheduler steps used the same normal RunAs flow over the existing SSH
channel. Only the idle administrator console created for this completed
preflight was closed; no pre-existing Windows/WSL/competition process was stopped.

Read-only `secedit /export /areas USER_RIGHTS` showed that Administrators already
had batch-logon rights and no batch-logon denial applied. Complete privilege
lines were compared before registration, after registration and after actual
S4U execution: **no user-right changes**, including no new explicit user grant.
The default scheduler ACL was retained. The ordinary filtered owner has read
access, not an unannounced management grant; maintenance disable was actually
verified in the original owner's normal administrator context before enabling.

At **01:49:05**, `TASK_CREATE | TASK_DISABLE` created only the previously absent
`PCR Relay WSL Guard`. Readback was disabled, state `1`, with zero instances and
no prior run. The exact staged SHA-256 remained
`f9dc445dc2f68ad47046a0d2f3ff953850a69050530fda73b9980ff3525520c3`.
Task Scheduler canonicalized the XML, omitting default `LeastPrivilege` and false
fields; native COM effective values independently confirmed S4U `2`, run level
`0`, one exact original-distribution sleep action, `IgnoreNew` `2`, `PT0S`, and
false idle/network/battery-block/battery-stop/wake/hard-terminate conditions.
The boot/logon delays and five-minute daily fallback were preserved.

At **01:50:11**, after separate readback and control checks, the task was enabled
and started. Actual instance **8B09BE4F-B900-4110-A39E-A244079E4226** runs in
Windows **Session 0** under the original SID, with native WSL launcher **23612**
and child **7016**. In the original distribution a single new Linux process
**53003**, UID **1000**, executes `/usr/bin/sleep infinity`. No relay, database or
FRP application was started by this task; they remain the original systemd
instances. At **01:51:08**, an additional native Run request left the same actual
instance GUID, engine PID and Linux sleep process in place. The returned request
object had another GUID, but **only the original actual instance remained**;
the request object is not counted as another running task.

Evidence includes `guard-register.json`, `registered-disabled.xml`,
`guard-activate.json`, `guard-repeat.json`, `guard-audit.json`, the before/after
rights exports and timestamped `guard-action-*` command records in the private
relay directory. Seven local guard tests were rerun successfully. No Windows
restart, logout, distribution shutdown, service restart, token manipulation,
new production bridge consumer or PCR command was used for validation.

The **01:53:54** competition snapshot
`snapshot-20261016T175354.281301Z.json` still has four groups, all **32 active
Agents running**, eight live owners, healthy pcrd **0.1.98/PID 48685** and the
same loaded macOS **0.1.439/PID 64075**. The existing relay remains connected
since **01:08:23.211645**, with the older 01:07:50 error preserved as history.
Outbox and accepted handoff are empty; the original one queued item retains its
identity, prompt hash and destination. No extra uninspected window is open.

**Deployment state is now installed and actually running under S4U, not merely
prepared.** This verifies the noninteractive caller in the currently running
Windows/WSL environment; cold boot, logged-out startup and real outage recovery
remain untested. They must not be simulated by interrupting competition work.
The guard maintains the original distribution while enabled, not host power,
network availability or application health. For deliberate WSL maintenance,
disable this exact task first using the original owner's administrator session;
do not change its ACL or stop unrelated tasks to avoid that explicit boundary.

### Actual Calendar Boundary And Status Semantics

The **01:55:01** real calendar request updated `LastRunTime` and reported
`LastTaskResult = 0x800710E0` (Win32 **4320**). It did not replace or duplicate the
original active instance: the **01:55:16** COM readback still has exactly one
running GUID/engine, the original Linux sleep remains, and all three enabled
triggers have `StopAtDurationEnd=false`. The calendar's effective interval is
`PT5M`, duration `P1D`.

This is consistent with `IgnoreNew` refusing an additional request while the
original instance runs, not evidence of a failed original caller. The specific
refusal reason is **not independently proven** by that generic code; Operational
history was already disabled and was not enabled for this check. The live
instance, correct Linux process and continuous original business services must
remain part of the health predicate. Never globally whitelist that code, hide
it, restart the distribution or replace `IgnoreNew` to make a status field green.
Microsoft documents [4320 as generic request refusal](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--4000-5999-)
and [IgnoreNew as keeping the existing instance](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-multipleinstances).

At **01:54:57-01:55:03**, `diagnosis-20261016T175503.379426Z.json` again records
public/source/upstream production health 200 and protected anonymous 401. WSL
boot ID **3876fb9d-cf11-4439-81e3-5b9d65890e70**, relay **440**, FRP **280**, and
database **259** plus all their start times match the pre-deployment 01:48
baseline exactly. The same authenticated socket remains connected with growing
bidirectional byte counts. Native before/after comparison also confirms all
eight competition owners, the loaded client process, receipt-store identity and
the queued item's complete saved projection are unchanged.

## Final Readback, 02:02

A fresh original-owner administrator **read-only** query at **02:02:18** acquired
the task anew, called `GetInstances(0)` and refreshed each instance. It still
finds exactly the original running GUID/engine. This does not rely on a cached
Run return object or a filtered token's limited instance visibility. The
02:00:01 calendar request is preserved with the same generic refusal status;
the original action remains active. See `guard-inspect_admin.json` and
`guard-action-20261016T180219.254523Z.json`. No additional Run or stop was used.

Both saved before/after-registration and before/after-execution USER_RIGHTS
exports also compare **byte-identical** (`cmp` exits 0), not only equal parsed
privilege lines. The private evidence directory remains mode 0700 and copied
evidence files mode 0600.

`snapshot-20261016T180249.182954Z.json` again confirms four groups and **32 running
Agents**, eight unchanged owners, unchanged loaded client, unchanged receipt
store and original queued item, empty outbox/handoff, no extra window and no
active run/worker/Agent error. `diagnosis-20261016T180258.348786Z.json` confirms
public production 200, anonymous protected 401, the original WSL boot ID and
all source service PIDs/start times, and the same authenticated socket with
continued bidirectional traffic. No competition task was stopped, resumed,
adopted or resubmitted by this maintenance.

The existing **PCR competition hourly monitor** (`pcr`) remains **ACTIVE** in
the same thread at its unchanged 60-minute interval. Its saved prompt was read
back after updating it to actual deployment, maintenance control, current
baseline and the refusal-code limits; stale "not registered" authorization
instructions were removed. Old update jobs were not resumed. Routine monitoring
must not rerun the one-time registration/activation/repeat/admin-audit steps.

The independent Pro follow-up accepted the installed/current-run conclusion,
distinguished request status from actual instance health, and suggested the
fresh/full-context query above. It is corroborating review, not production
evidence. Screenshot: `web-deployed-guard-review.png` in the private directory.
No no-instance automatic calendar recovery or logged-out cold boot is claimed.
Further interruption-based tests are intentionally excluded during competition.
