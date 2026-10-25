"""Quiesce a legacy supervisor only after a separately supplied safety audit.

This helper never creates or clears user stop files. It is not an automatic
retry: callers must have explicit permission to interrupt the named batch.
"""

import os
import signal
import subprocess
import time


def processes():
    rows = {}
    output = subprocess.check_output(
        ["ps", "-ww", "-ax", "-o", "pid=,ppid=,pgid=,stat=,etime=,comm="], text=True)
    for line in output.splitlines():
        parts = line.split(None, 5)
        if len(parts) == 6:
            pid, parent, group, status, elapsed, command = parts
            try:
                rows[int(pid)] = {"pid": int(pid), "parent": int(parent), "group": int(group),
                    "status": status, "elapsed": elapsed, "command": command.strip()}
            except ValueError:
                pass
    return rows


def signature(row):
    return {key: value for key, value in row.items() if key not in {"status", "elapsed"}}


def descendants(rows, roots):
    selected = set(roots)
    while True:
        extra = {pid for pid, row in rows.items() if row["parent"] in selected}
        if extra <= selected:
            return selected
        selected |= extra


def quiesce(supervisor, adapters, native_servers, audit, *, timeout=20):
    """Freeze creation, audit, drain adapters, then retire only their supervisor."""
    initial = processes()
    targets = [supervisor, *adapters, *native_servers]
    if len(set(targets)) != len(targets) or any(pid not in initial for pid in targets):
        raise RuntimeError("Pause requires distinct, currently owned processes")
    if (any(initial[pid]["parent"] != supervisor for pid in adapters)
        or not set(native_servers) <= descendants(initial, adapters)):
        raise RuntimeError("Pause processes do not belong to this supervisor")
    identities = {pid: signature(initial[pid]) for pid in targets}
    frozen = set()

    def send(pid, signum):
        row = processes().get(pid)
        if row is None or signature(row) != identities[pid] or "Z" in row["status"]:
            raise RuntimeError(f"Process identity changed before signal: {pid}")
        os.kill(pid, signum)

    try:
        for pid in native_servers:
            send(pid, signal.SIGSTOP)
            frozen.add(pid)
        time.sleep(0.5)  # Drain native events already handed to the old adapter.
        audit("frozen", processes())
        send(supervisor, signal.SIGSTOP)
        frozen.add(supervisor)
        audit("before-drain", processes())
        for pid in adapters:
            send(pid, signal.SIGTERM)
        for pid in native_servers:
            send(pid, signal.SIGCONT)
            frozen.remove(pid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = processes()
            if all(pid not in rows or "Z" in rows[pid]["status"] for pid in [*adapters, *native_servers]):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("The dialogue processes did not drain; supervisor will be restored")
        audit("drained", processes())
        send(supervisor, signal.SIGKILL)
        frozen.remove(supervisor)
        return {"supervisor": identities[supervisor], "adapters": [identities[pid] for pid in adapters],
            "native_servers": [identities[pid] for pid in native_servers], "retired": True,
            "stop_files_created_or_cleared": False}
    finally:
        for pid in frozen:
            row = processes().get(pid)
            if row is not None and signature(row) == identities[pid] and "Z" not in row["status"]:
                os.kill(pid, signal.SIGCONT)

