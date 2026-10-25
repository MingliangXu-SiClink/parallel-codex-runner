"""Resume receipt-proven candidates at an idle or verified dead-owner boundary."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

from parallel_codex_runner_core.app import retained_execution_preparation
from parallel_codex_runner_core.plugin_runtime import PluginRunManager
from parallel_codex_runner_core.workspace import agent_execution_workspace


def inspect(state_dir, run_id, agents):
    state = json.loads((state_dir / "runs.json").read_text(encoding="utf-8"))
    run = next(value for value in state["runs"] if value["run_id"] == run_id)
    root = Path(run["run_root"])
    bindings = {}
    for idx in agents:
        workspace = agent_execution_workspace(Path(run["workspace"]), root / "workspaces", idx,
            str(run["config"].get("workspace_mode") or "isolated"))
        _command, home, fingerprint = retained_execution_preparation(root, idx, workspace, run["prompt"])
        receipt = json.loads((home.parent / "execution_recovery.json").read_text(encoding="utf-8"))
        bindings[str(idx)] = {"fingerprint": fingerprint, "workspace": str(workspace), "codex_home": str(home),
            "thread_id": receipt["thread_id"], "turn_id": receipt["turn_id"],
            "client_message_id": receipt["client_message_id"], "stage": receipt["stage"]}
    return {"run_id": run_id, "conversation_id": run["conversation_id"], "run_root": str(root),
        "prompt_sha256": hashlib.sha256(run["prompt"].encode()).hexdigest(),
        "config_sha256": hashlib.sha256(json.dumps(run["config"], sort_keys=True).encode()).hexdigest(),
        "status": run["status"], "worker_pid": run["worker_pid"], "worker_state": run["worker_state"],
        "agent_statuses": run["agent_statuses"], "bindings": bindings, "submitted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agents", type=int, nargs="+", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--orphaned-worker-pid", type=int)
    parser.add_argument("--orphaned-operation-id")
    args = parser.parse_args()
    if len(set(args.agents)) != len(args.agents) or any(idx <= 0 for idx in args.agents):
        parser.error("agents must be distinct positive indices")
    before = inspect(args.state_dir, args.run_id, args.agents)
    if not args.execute:
        print(json.dumps(before, ensure_ascii=False, indent=2))
        return 0
    orphaned = args.orphaned_worker_pid is not None and args.orphaned_operation_id is not None
    if (args.orphaned_worker_pid is None) != (args.orphaned_operation_id is None):
        parser.error("orphaned owner PID and operation ID must be supplied together")
    if not orphaned and (before["status"] != "completed" or before["worker_state"] != "idle"):
        print(json.dumps({"submitted": False, "reason": "original batch is not idle", "run_id": args.run_id}))
        return 75
    version = importlib.metadata.version("parallel-codex-runner")
    if version != "0.1.92":
        raise RuntimeError("Execute this recovery only with the independently verified 0.1.92 runtime")
    manager = PluginRunManager(args.state_dir, detached_workers=True, run_maintenance=False)
    try:
        result = (manager.resume_interrupted_batch(args.run_id, args.agents,
            old_pid=args.orphaned_worker_pid, old_operation_id=args.orphaned_operation_id)
            if orphaned else manager.resume_agents_in_place(args.run_id, args.agents))
        print(json.dumps({"before": before, "submitted": True, "runtime": version,
            "run_id": result["run_id"], "worker_pid": result["worker_pid"],
            "operation_id": result["recovery_operation_id"]}, ensure_ascii=False, indent=2))
    finally:
        manager.close(wait_seconds=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
