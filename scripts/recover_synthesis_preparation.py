"""Explicit, fingerprint-bound recovery of synthesis slots that never reached Codex."""

import argparse
import hashlib
import importlib.metadata
import json
import time
from pathlib import Path

from parallel_codex_runner_core.plugin.progress import result_fingerprint
from parallel_codex_runner_core.plugin.lifecycle import pid_is_alive
from parallel_codex_runner_core.plugin_runtime import PluginRunManager
from parallel_codex_runner_core.remote.ipc import PcrdClient
from parallel_codex_runner_core.synthesis import failed_synthesis_preparation_fingerprint


def inspect(state_dir, run_id, agents):
    data = json.loads((state_dir / "runs.json").read_text())
    run = next(row for row in data["runs"] if row["run_id"] == run_id)
    root = Path(run["run_root"])
    candidates = {idx: result for idx, result in run["results"].items() if result.get("role") == "candidate"}
    report = {"run_id": run_id, "conversation_id": run["conversation_id"], "run_root": run["run_root"],
        "status": run["status"], "worker_pid": run["worker_pid"], "worker_state": run["worker_state"],
        "config_sha256": result_fingerprint(run["config"]), "prompt_sha256": hashlib.sha256(run["prompt"].encode()).hexdigest(),
        "results_sha256": result_fingerprint(run["results"]),
        "bindings": {str(idx): failed_synthesis_preparation_fingerprint(root, idx, run["prompt"]) for idx in agents},
        "candidate_answers": {idx: hashlib.sha256(Path(value["final_message"]).read_bytes()).hexdigest() for idx, value in candidates.items()}}
    return report, result_fingerprint(report)


def observe_retired_owner(client, run_id, old_pid, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        actual = client.request("runs.get", {"run_id": run_id, "include_events": False})
        if (actual.get("worker_pid") is not None or actual.get("worker_state") != "stopped" or actual.get("worker_active")):
            raise RuntimeError("The existing daemon has not observed the retired owner")
        if not pid_is_alive(old_pid):
            # Confirm again after exit, so the daemon reaps any cached Popen
            # before it can mistake that old exit for the replacement's death.
            final = client.request("runs.get", {"run_id": run_id, "include_events": False})
            if final.get("worker_pid") is None and final.get("worker_state") == "stopped" and not final.get("worker_active"):
                return
            raise RuntimeError("The retired owner changed before publication")
        if time.monotonic() >= deadline:
            raise RuntimeError("The idle owner acknowledged retirement but has not exited")
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--daemon-socket", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agents", type=int, nargs="+", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if not args.agents or len(set(args.agents)) != len(args.agents) or any(idx <= 0 for idx in args.agents):
        parser.error("agents must be distinct positive synthesis slots")
    before, plan = inspect(args.state_dir, args.run_id, args.agents)
    if not args.execute:
        print(json.dumps({"plan": before, "plan_sha256": plan, "submitted": False}, indent=2))
        return 0
    if plan != args.expected_plan_sha256:
        raise RuntimeError("The inspected recovery identity changed; nothing was submitted")
    if importlib.metadata.version("parallel-codex-runner") != "0.1.97":
        raise RuntimeError("Use the independently validated 0.1.97 runtime")
    client = PcrdClient(args.daemon_socket, timeout=30)
    client.request("runs.get", {"run_id": args.run_id, "include_events": False})
    manager = PluginRunManager(args.state_dir, detached_workers=True, run_maintenance=False)

    try:
        if inspect(args.state_dir, args.run_id, args.agents)[1] != plan:
            raise RuntimeError("Recovery identity changed during daemon reconciliation")
        result = manager.recover_synthesis_preparation(args.run_id, args.agents,
            owner_retired=lambda: observe_retired_owner(client, args.run_id, before["worker_pid"]))
        print(json.dumps({"run_id": args.run_id, "worker_pid": result["worker_pid"],
            "operation_id": result["recovery_operation_id"], "indices": args.agents,
            "runtime": "0.1.97", "submitted": True, "daemon_restarted": False}, indent=2))
    finally:
        manager.close(wait_seconds=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
