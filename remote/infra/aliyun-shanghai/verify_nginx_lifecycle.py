"""Exercise the drop-in against an isolated loopback Nginx, never nginx.service."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True, timeout=30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop-in", type=Path, required=True)
    args = parser.parse_args()
    assert os.getuid() == 0
    root = Path(tempfile.mkdtemp(prefix="pcr-nginx-lifecycle-", dir="/run"))
    unit_name = root.name + ".service"
    unit = Path("/run/systemd/system") / unit_name
    drop_dir = Path(str(unit) + ".d")
    pid_file = root / "nginx.pid"
    config = root / "nginx.conf"
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    config.write_text(
        f"pid {pid_file}; error_log {root}/error.log notice;\n"
        "worker_processes 1; events { worker_connections 32; }\n"
        'http { lua_package_path "/www/server/nginx/lib/lua/?.lua;;";\n'
        "access_log off; server {\n"
        f"listen 127.0.0.1:{port};\n"
        'location / { return 200 "pcr-lifecycle-ok"; }\n'
        'location /slow { limit_rate 20; return 200 "' + "x" * 2000 + '"; }\n'
        "} }\n"
    )
    template = args.drop_in.read_text()
    template = template.replace("/www/server/nginx/logs/nginx.pid", str(pid_file))
    template = template.replace("/www/server/nginx/conf/nginx.conf", str(config))
    unit.write_text(
        "[Unit]\nDescription=Isolated PCR Nginx lifecycle regression\n"
        "[Service]\nType=forking\nRestart=no\nKillMode=process\n"
        "GuessMainPID=no\nRemainAfterExit=yes\nSuccessExitStatus=5 6\n"
        "ExecStart=/bin/false\nExecStop=/bin/false\nExecReload=/bin/false\n"
    )
    drop_dir.mkdir()
    (drop_dir / "lifecycle.conf").write_text(template)
    results = {"unit": unit_name, "port": port, "restart_pids": []}

    def main_pid():
        return int(run("systemctl", "show", unit_name, "-p", "MainPID", "--value").stdout)

    def request():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            assert response.read() == b"pcr-lifecycle-ok"

    try:
        run("systemd-analyze", "verify", str(unit))
        run("systemctl", "daemon-reload")
        run("systemctl", "start", unit_name)
        pid = main_pid()
        assert pid > 0 and int(pid_file.read_text()) == pid
        request()
        for _ in range(3):
            old_pid = pid
            run("systemctl", "restart", unit_name)
            pid = main_pid()
            assert pid > 0 and pid != old_pid and not Path(f"/proc/{old_pid}").exists()
            request()
            results["restart_pids"].append([old_pid, pid])
        run("systemctl", "reload", unit_name)
        assert main_pid() == pid
        request()
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.sendall(b"GET /slow HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            assert connection.recv(4096).startswith(b"HTTP/1.1 200")
            started = time.monotonic()
            stopping = subprocess.Popen(["systemctl", "stop", unit_name])
            try:
                time.sleep(0.25)
                assert stopping.poll() is None, "stop returned before the active request drained"
                connection.close()
                assert stopping.wait(timeout=10) == 0
            finally:
                connection.close()
                if stopping.poll() is None:
                    stopping.wait(timeout=15)
            results["graceful_stop_seconds"] = time.monotonic() - started
        assert not Path(f"/proc/{pid}").exists() and main_pid() == 0
        config.write_text("invalid_test_directive;\n")
        assert run("systemctl", "start", unit_name, check=False).returncode != 0
        assert run("systemctl", "is-active", unit_name, check=False).stdout.strip() == "failed"
        results["invalid_config_rejected"] = True
        results["passed"] = True
        print(json.dumps(results, sort_keys=True))
    finally:
        # Stop only our random test unit before removing its runtime files.
        run("systemctl", "stop", unit_name, check=False)
        run("systemctl", "reset-failed", unit_name, check=False)
        unit.unlink(missing_ok=True)
        shutil.rmtree(drop_dir)
        run("systemctl", "daemon-reload")
        shutil.rmtree(root)


if __name__ == "__main__":
    main()
