# Relay ingress restart race

## Evidence

At 07:46 CST, authenticated macOS 485 catalog access failed before issuing a
download ticket. Public TLS returned `SSL_ERROR_SYSCALL`; this was not a pcrd
or model-authentication failure. The WSL Relay 0.1.21 remained active (PID
1264922, unchanged start at 2026-10-27 12:45:34 CST), and its loopback `/healthz`
returned 200 in 10 ms. Public port 443 had no listener. FRP's original 39080
listener was present. Certificate renewal had succeeded at 03:56; no renewal
policy or timer was changed in this repair.

Nginx's journal at 06:25:52 shows a stop followed immediately by start. Its
SysV stop sent a signal and returned before the old master/workers exited.
The generated systemd unit had no PIDFile, `GuessMainPID=no`,
`RemainAfterExit=yes`, and `KillMode=process`. Start saw the still-exiting old
master and failed with "already running"; the old master subsequently exited,
leaving no HTTPS listener. The initiator of that restart is not yet known.
The recurring lifecycle defect is demonstrated; not every historical disconnect
has been attributed to it.

## Fix and validation

`remote/infra/aliyun-shanghai/nginx-lifecycle.conf` overrides the existing unit,
not its routes, authentication, DB, or certificate. The original binary/config
remain in place. Systemd tracks the real master by its PID file and waits for
graceful SIGQUIT completion. No automatic restart, watchdog, cron, or new timer
is added. A stalled drain cannot force-kill requests or start a competing master.
Start/reload bypass the panel init script's unrelated configuration rewrite.
These settings follow the systemd
[service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
and [kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)
contracts; validation below uses the installed systemd 255 and Nginx, not docs alone.

An isolated loopback unit passed three stop/start cycles (master PIDs
306804 -> 306814 -> 306822 -> 306830), same-master reload, a live slow-response
graceful drain (0.269 s after the client closed), and invalid-config rejection.
Its runtime-only unit/config/port were removed. The first test fixture lacked
this build's Lua module search path and failed before start; the fixture was
corrected to use the installed module path. That failed test is not a pass.

Deployment first stopped at two safety/validation checks without starting
anything: the old PID file was empty rather than absent, and systemd-analyze
needed the generated unit's explicit path. After checking those facts, the
same reviewed drop-in was validated at
`/run/systemd/generator.late/nginx.service`. Immediately before activation,
the production service remained failed, MainPID=0, and 443 unbound. No healthy
ingress process was restarted.

At 07:57:47 CST, the original service started successfully, master PID 307394,
active/running, tracked cgroup `/system.slice/nginx.service`. All original main,
HTTP vhost, and stream config hashes were unchanged. Public TLS (normal trust
validation, no insecure option) then returned `/healthz` 200 in 76 ms. The
authenticated application download/installation is a separate acceptance gate.
WSL, Relay, pcrd, workers and research jobs were not restarted by this repair.

Private activation hashes and boundary checks are in
`InteractivePerformance-20261027-39zHuL/nginx-lifecycle-activation.json` under
the existing ParallelCodexMaintenance cache. No secret or full route config is
copied into this repository. Cold boot/power-loss recovery remains untested.
