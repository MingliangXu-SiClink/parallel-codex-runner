# Relay TLS renewal

TLS for `https://8.153.193.1` terminates on `aliyun-shanghai`, not the WSL
Relay process. Nginx serves HTTP-01 challenges from `/var/www/pcr-acme`.

The IP certificate requires `/opt/certbot-ip/bin/certbot` (5.7.0 at deployment).
The distribution's Certbot 2.9.0 cannot renew this IP certificate. Keep the
existing `pcr-relay-cert-renew.timer`; disable the duplicate `certbot.timer`
only after confirming it has no other certificates to maintain.

The panel's `IN_BT_Country` geographic filter precedes UFW and blocks the
certificate authority's international validators even though UFW allows port
80. Install `pcr-acme-validation-window` as a root-owned executable at
`/usr/local/sbin/`, and `validation-window.conf` at
`/etc/systemd/system/pcr-relay-cert-renew.service.d/validation-window.conf`.
Reload systemd. Renewal must run through that service, not bare Certbot.
The renewal drop-in requires Nginx as well as ordering after it: `After=` alone
does not start a stopped or failed web server. A timer-triggered renewal now
starts the existing Nginx service before opening the temporary validation window.
It does not restart an already running server or change its shared routes.

The exception exists only during renewal, affects only TCP 80's geographic
filter, and does not bypass IP blocklists or UFW. `ExecStopPost` removes it on
success, failure, timeout, or service stop. Nginx continues to expose only the
ACME path over HTTP; ordinary requests redirect to HTTPS. No TLS verification
is disabled. The existing deploy hook reloads Nginx after successful renewal.

Verify with a Certbot staging dry-run inside a transient systemd service
using the same pre/stop hooks, then start `pcr-relay-cert-renew.service`.
Check public HTTPS without `--insecure`, the served certificate expiry, timer
status, and absence of the `pcr-relay-acme-validation` iptables rule afterward.

## Nginx lifecycle

The panel's SysV script sends a stop signal without waiting. Its generated unit
has no main PID and reports itself active after exit. A systemd restart can
therefore attempt to start while the old master is still exiting, fail with
"already running", then leave no listener once the old master exits.

Install `nginx-lifecycle.conf` at
`/etc/systemd/system/nginx.service.d/pcr-lifecycle.conf`. It keeps the original
binary, configuration, routes, certificate, and existing service. A PID file
tracks the actual master; systemd waits for graceful SIGQUIT completion before
starting again. It does not enable automatic restarts or create a scheduler.
After a stop timeout it does not force-kill outstanding requests or launch a
second master. Start/reload no longer run the panel script that rewrites config.

Before installation, run `verify_nginx_lifecycle.py --drop-in
nginx-lifecycle.conf` as root on this host. It uses a random runtime-only service,
an isolated loopback port and config, and removes its test files afterward. It
checks repeated restart, PID tracking, graceful request drain, reload, and
invalid configuration failure. Never use the real service for restart tests.
Apply at an already stopped boundary or a separately approved maintenance
boundary; do not restart a healthy shared ingress just to validate this change.
