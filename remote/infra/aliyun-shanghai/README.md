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

The exception exists only during renewal, affects only TCP 80's geographic
filter, and does not bypass IP blocklists or UFW. `ExecStopPost` removes it on
success, failure, timeout, or service stop. Nginx continues to expose only the
ACME path over HTTP; ordinary requests redirect to HTTPS. No TLS verification
is disabled. The existing deploy hook reloads Nginx after successful renewal.

Verify with a Certbot staging dry-run inside a transient systemd service
using the same pre/stop hooks, then start `pcr-relay-cert-renew.service`.
Check public HTTPS without `--insecure`, the served certificate expiry, timer
status, and absence of the `pcr-relay-acme-validation` iptables rule afterward.
