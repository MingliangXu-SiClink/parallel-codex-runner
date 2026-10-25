#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from parallel_codex_runner_core.remote.ipc import PcrdClient


def wait_for_server(process: subprocess.Popen[bytes], token_path: Path) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"pcrd exited with status {process.returncode}")
        if token_path.is_file():
            try:
                PcrdClient(token_path.parent / "pcrd.sock", timeout=1).request(
                    "system.health"
                )
                return
            except Exception:
                pass
        time.sleep(0.1)
    raise RuntimeError("pcrd did not become ready")


def start_server(state_dir: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "parallel_codex_runner_core.remote.daemon",
            "--state-dir",
            str(state_dir),
            "serve",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    wait_for_server(process, state_dir / "pcrd.token")
    return process


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pcr-remote-smoke-") as tmp:
        root = Path(tmp)
        state_dir = root / "state"
        first_workspace = root / "workspace-a"
        second_workspace = root / "workspace-b"
        first_workspace.mkdir()
        second_workspace.mkdir()

        process = start_server(state_dir)
        try:
            client = PcrdClient(state_dir / "pcrd.sock", timeout=5)
            first = client.request(
                "workspaces.add",
                {"path": str(first_workspace), "select": False},
                command_id="smoke-add-workspace-a",
            )
            replayed = client.request(
                "workspaces.add",
                {"path": str(first_workspace), "select": False},
                command_id="smoke-add-workspace-a",
            )
            first_id = str((first.get("workspace") or {}).get("workspace_id") or "")
            replayed_id = str(
                (replayed.get("workspace") or {}).get("workspace_id") or ""
            )
            if not first_id or replayed_id != first_id:
                raise RuntimeError("A replayed mutation changed workspace identity")
            client.request(
                "workspaces.add",
                {"path": str(second_workspace), "select": True},
                command_id="smoke-add-workspace-b",
            )
            listed = client.request("workspaces.list")
            if len(listed.get("workspaces") or []) != 2:
                raise RuntimeError("pcrd did not preserve two independent workspaces")
        finally:
            stop_server(process)

        restarted = start_server(state_dir)
        try:
            client = PcrdClient(state_dir / "pcrd.sock", timeout=5)
            listed = client.request("workspaces.list")
            capabilities = client.request("system.capabilities")
            if len(listed.get("workspaces") or []) != 2:
                raise RuntimeError("Workspace registry did not survive daemon restart")
            required = {
                "supports_multiple_workspaces": True,
                "supports_run_list_cursor": True,
                "supports_byte_bounded_pages": True,
            }
            for key, expected in required.items():
                if capabilities.get(key) is not expected:
                    raise RuntimeError(f"Missing capability: {key}")
            print(
                json.dumps(
                    {
                        "ok": True,
                        "workspaces": 2,
                        "daemon_version": capabilities.get("daemon_version"),
                    },
                    sort_keys=True,
                )
            )
        finally:
            stop_server(restarted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
