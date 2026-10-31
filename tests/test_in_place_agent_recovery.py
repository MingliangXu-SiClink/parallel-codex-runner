import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from parallel_codex_runner_core import app, plugin_runtime
from parallel_codex_runner_core.plugin import worker
from parallel_codex_runner_core.plugin import lifecycle
from parallel_codex_runner_core.plugin.lifecycle import WORKER_PROTOCOL_VERSION
from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.remote.controller import RemoteController
from parallel_codex_runner_core.remote.ipc import PcrdClient, PcrdServer


@unittest.skipUnless(os.name == "posix", "Retained execution ownership uses POSIX locks")
class InPlaceRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        workspace = root / "workspace"
        workspace.mkdir()
        run_root = root / "runs" / "20261015_000000"
        prompt, instructions = "original expensive task", "original permissions"
        results = {}
        for idx in (1, 2):
            branch = run_root / "workspaces" / f"agent_{idx:03d}"
            branch.mkdir(parents=True)
            (branch / "trained.bin").write_bytes(b"completed experiment")
            meta = run_root / "meta" / f"agent_{idx:03d}"
            home = meta / "codex_home"
            sessions = home / "sessions"
            sessions.mkdir(parents=True)
            thread_id = f"thread-{idx}"
            rollout = sessions / f"rollout-{thread_id}.jsonl"
            rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": thread_id}}) + "\n")
            with sqlite3.connect(home / "state_5.sqlite") as db:
                db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
                db.execute("INSERT INTO threads VALUES (?, ?)", (thread_id, str(rollout)))
            command = ["codex", "exec", "resume", "-o", str(meta / "final_message.md"), thread_id, "-"]
            recorded = {"idx": idx, "role": "candidate", "cwd": str(branch), "codex_home": str(home),
                        "command": command, "additional_developer_instructions": instructions}
            result = {"idx": idx, "role": "candidate", "workspace_dir": str(branch), "meta_dir": str(meta),
                      "codex_home": str(home), "command": command, "status": "failed", "returncode": 1,
                      "stdout_log": str(meta / "stdout.log"), "stderr_log": str(meta / "stderr.log"),
                      "final_message": str(meta / "final_message.md"), "seconds": 100,
                      "codex_thread_id": thread_id}
            identity = hashlib.sha256(json.dumps([command, str(branch), str(home), prompt, instructions],
                                                ensure_ascii=False).encode()).hexdigest()
            receipt = {"version": 1, "identity": identity, "stage": "blocked", "thread_id": thread_id,
                       "turn_id": f"old-turn-{idx}", "client_message_id": f"original-input-{idx}",
                       "native_pid": 999999999, "recoveries": 0,
                       "error": {"message": "workspace routing discovery timed out", "codexErrorInfo": "other"}}
            for name, value in (("command.json", recorded), ("status.json", result),
                                ("execution_recovery.json", receipt), ("developer_instructions.json", instructions)):
                (meta / name).write_text(json.dumps(value))
            (meta / "stdout.log").write_text("all original tool events\n")
            (meta / "stderr.log").write_text("original error\n")
            (meta / "execution_recovery.lock").touch()
            results[idx] = result
        run = ManagedRun(run_id="pcr-20261015T000000Z-12345678", workspace=str(workspace), prompt=prompt,
            config={"num_agents": 2, "synthesis_agents": 3, "workspace_mode": "isolated",
                    "resume_session_id": "original-parent", "run_base": str(run_root.parent)},
            status="completed", run_root=str(run_root), results=results,
            agent_statuses={1: "failed", 2: "failed"}, agent_roles={1: "candidate", 2: "candidate"},
            worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="idle", worker_pid=os.getpid(),
            artifact_token="retained-execution-fixture")
        return run

    def preparation(self, run, idx=1):
        root = Path(run.run_root)
        return app.retained_execution_preparation(root, idx, root / "workspaces" / f"agent_{idx:03d}", run.prompt)

    def manager(self, root, run):
        manager = plugin_runtime.PluginRunManager(root / "state", detached_workers=True, run_maintenance=False)
        manager._runs[run.run_id] = run
        manager._write_run_marker_locked(run)
        checkpoint = {"run_id": run.run_id, "pid": run.worker_pid, "protocol": WORKER_PROTOCOL_VERSION,
                      "alive": True, "active": False, "operation": "", "operation_id": "",
                      "last_operation": "initial", "finished_at": "2026-10-15T00:10:00Z"}
        target = manager.state_dir / "workers" / f"{run.run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(checkpoint))
        return manager, checkpoint, target

    def test_retained_preparation_keeps_workspace_database_rollout_logs_and_original_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            command, home, fingerprint = self.preparation(run)
            paths = [home / "state_5.sqlite", home / "sessions/rollout-thread-1.jsonl",
                     Path(run.run_root) / "workspaces/agent_001/trained.bin", home.parent / "stdout.log"]
            before = [(path.stat().st_ino, path.read_bytes()) for path in paths]
            with patch.object(app, "copy_codex_support_entries") as support:
                actual = app._prepare_retained_execution(run_root=Path(run.run_root), idx=1,
                    workspace=Path(run.results[1]["workspace_dir"]), prompt=run.prompt,
                    fingerprint=fingerprint, real_codex_home=root / "source-home")
            self.assertEqual(actual, (command, home))
            self.assertEqual([(path.stat().st_ino, path.read_bytes()) for path in paths], before)
            self.assertEqual(json.loads((home.parent / "execution_recovery.json").read_text())["stage"], "blocked")
            self.assertEqual(len(list((Path(run.run_root) / "retry_history/agent_001").glob("*/status.json"))), 1)
            support.assert_called_once_with(root / "source-home", home)

    def test_existing_execution_owner_prevents_preparation_before_any_support_copy(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            _, home, fingerprint = self.preparation(run)
            with (home.parent / "execution_recovery.lock").open("r+b") as owner:
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(app, "copy_codex_support_entries") as support:
                    with self.assertRaises(BlockingIOError):
                        app._prepare_retained_execution(run_root=Path(run.run_root), idx=1,
                            workspace=Path(run.results[1]["workspace_dir"]), prompt=run.prompt,
                            fingerprint=fingerprint, real_codex_home=root / "source-home")
                    support.assert_not_called()
                self.assertFalse((Path(run.run_root) / "retry_history").exists())

    def test_normal_retry_prefers_retained_native_execution_and_does_not_rebuild_accepted_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            manager, _, _ = self.manager(root, run)
            try:
                with patch.object(manager, "_start_additional_batch", return_value={}) as start:
                    manager.retry_agent(run.run_id, 1)
                    self.assertEqual(set(start.call_args.kwargs["retained_execution_fingerprints"]), {1})
                receipt_path = Path(run.results[1]["meta_dir"]) / "execution_recovery.json"
                receipt = json.loads(receipt_path.read_text())
                receipt["identity"] = "not-the-original-task"
                receipt_path.write_text(json.dumps(receipt))
                with patch.object(manager, "_start_additional_batch") as start:
                    with self.assertRaises(plugin_runtime.PluginRunError):
                        manager.retry_agent(run.run_id, 1)
                    start.assert_not_called()
                self.assertEqual((Path(run.results[1]["workspace_dir"]) / "trained.bin").read_bytes(), b"completed experiment")
            finally:
                manager.close(wait_seconds=0)

    def test_active_candidate_channel_requires_retained_capability_and_rejects_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            manager, checkpoint, checkpoint_path = self.manager(root, run)
            run.status, run.worker_state, run.worker_operation = "running", "busy", "initial"
            checkpoint.update(active=True, operation="initial")
            checkpoint_path.write_text(json.dumps(checkpoint))
            channel = lifecycle.worker_candidate_requests_path(manager.state_dir, run.run_id)
            lifecycle.write_json_atomic(channel, {"accepting": True, "requests": []})
            try:
                self.assertFalse(manager.get_run(run.run_id)["agents"][0]["can_retry"])
                with self.assertRaisesRegex(plugin_runtime.PluginRunError, "does not support in-place retry"):
                    manager.retry_agent(run.run_id, 1)
                self.assertEqual(lifecycle.read_json(channel)["requests"], [])
                self.assertEqual(run.agent_statuses[1], "failed")
                lifecycle.initialize_candidate_requests(channel)
                run.results[2]["role"] = run.agent_roles[2] = "synthesis"
                meta = Path(run.results[2]["meta_dir"])
                for name in ("status.json", "command.json"):
                    value = json.loads((meta / name).read_text())
                    value["role"] = "synthesis"
                    (meta / name).write_text(json.dumps(value))
                self.assertFalse(manager.get_run(run.run_id)["agents"][1]["can_retry"])
                with self.assertRaisesRegex(plugin_runtime.PluginRunError, "Synthesis retry must wait"):
                    manager.retry_agent(run.run_id, 2)
                manager.retry_agent(run.run_id, 1)
                self.assertEqual(lifecycle.read_json(channel)["requests"], [{"idx": 1, "retry": True}])
                self.assertEqual(run.worker_pid, os.getpid())
                self.assertEqual(run.worker_operation, "initial")
                self.assertFalse((manager.control_dir / run.run_id / "stop").exists())
                original = Path(run.results[2]["workspace_dir"]) / "trained.bin"
                self.assertEqual(original.read_bytes(), b"completed experiment")
            finally:
                manager.close(wait_seconds=0)

    def test_account_summary_distinguishes_current_legacy_and_replaced_native_owners(self):
        from parallel_codex_runner_core.codex_auth import SOURCE_ENV, file_auth
        from scripts.verify_account_follow_native import fixture_auth

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            source = root / "selected"
            source.mkdir()
            (source / "auth.json").write_text(json.dumps(fixture_auth("selected")))
            manager, _, _ = self.manager(root, run)
            try:
                with patch.dict(os.environ, {SOURCE_ENV: str(source)}, clear=True):
                    for idx in (1, 2):
                        run.agent_statuses[idx] = "running"
                        path = Path(run.results[idx]["meta_dir"]) / "execution_recovery.json"
                        state = json.loads(path.read_text())
                        state.update(stage="accepted", native_pid=os.getpid())
                        if idx == 1:
                            state["auth"] = {"identity": file_auth(source).identity, "status": "current", "native_pid": os.getpid()}
                        path.write_text(json.dumps(state))
                    summary = manager.account_execution_status()
                    self.assertEqual({key: summary[key] for key in ("current", "waiting", "legacy", "unverified")},
                                     {"current": 1, "waiting": 0, "legacy": 1, "unverified": 0})
                    path = Path(run.results[1]["meta_dir"]) / "execution_recovery.json"
                    state = json.loads(path.read_text())
                    state["auth"]["native_pid"] = 999999999
                    path.write_text(json.dumps(state))
                    self.assertEqual(manager.account_execution_status()["unverified"], 1)
                    self.assertNotIn("selected", json.dumps(summary))
            finally:
                manager.close(wait_seconds=0)

    def test_missing_result_requires_an_accepted_dead_native_receipt(self):
        for stage, allowed in (("accepted", True), ("continuation_ready", True), ("blocked", False),
                               ("ready", False), ("stopped", False)):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                run = self.fixture(Path(tmp).resolve())
                meta = Path(run.results[1]["meta_dir"])
                (meta / "status.json").unlink()
                receipt = json.loads((meta / "execution_recovery.json").read_text())
                receipt.update(stage=stage, error=None)
                (meta / "execution_recovery.json").write_text(json.dumps(receipt))
                if allowed:
                    self.assertEqual(self.preparation(run)[0], run.results[1]["command"])
                else:
                    with self.assertRaises(ValueError):
                        self.preparation(run)

    def test_dead_owner_recovery_rejects_stop_changed_owner_new_task_and_queued_work(self):
        for change in ("live-owner", "owner-id", "stop", "agent-stop", "rejected", "later-run", "queue",
                       "partial-candidates", "changed-input", "late-stop", "no-request"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                run = self.fixture(root)
                manager, checkpoint, path = self.manager(root, run)
                old_pid, operation_id = 999999999, run.run_id + "-old"
                run.status, run.worker_state, run.worker_pid = "interrupted", "crashed", None
                checkpoint.update(pid=old_pid, active=True, operation="initial", operation_id=operation_id)
                path.write_text(json.dumps(checkpoint))
                requests = manager.state_dir / "workers/requests"
                requests.mkdir()
                request_path = requests / f"{operation_id}.json"
                request = {"protocol": WORKER_PROTOCOL_VERSION,
                    "operation": "initial", "operation_id": operation_id, "run": run.to_dict()}
                if change == "changed-input":
                    request["run"]["prompt"] = "not the original request"
                request_path.write_text(json.dumps(request))
                control = manager.control_dir / run.run_id
                control.mkdir(parents=True)
                if change == "live-owner":
                    old_pid = os.getpid()
                elif change == "owner-id":
                    checkpoint["operation_id"] = "other-operation"
                    path.write_text(json.dumps(checkpoint))
                elif change in {"stop", "agent-stop"}:
                    (control / ("stop" if change == "stop" else "kill-agent-001")).touch()
                elif change == "rejected":
                    run.rejected_agents = [1]
                elif change == "later-run":
                    later = ManagedRun(run_id="pcr-20261015T000100Z-12345678", workspace=run.workspace,
                        prompt="new request", config={}, created_at="9999-01-01T00:00:00Z")
                    manager._runs[later.run_id] = later
                elif change == "queue":
                    (requests / f"{operation_id}-new.json").write_text("{}")
                elif change == "no-request":
                    request_path.unlink()

                def validate(_args):
                    if change == "late-stop":
                        (control / "stop").touch()

                try:
                    with patch.object(plugin_runtime, "validate_args", side_effect=validate), \
                         patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
                         patch.object(plugin_runtime, "spawn_worker") as spawn:
                        with self.assertRaises((plugin_runtime.PluginRunError, ValueError)):
                            manager.resume_interrupted_batch(run.run_id,
                                [1] if change == "partial-candidates" else [1, 2],
                                old_pid=old_pid, old_operation_id=operation_id)
                        spawn.assert_not_called()
                    self.assertFalse((manager.state_dir / "workers/retired").exists())
                    if change in {"stop", "late-stop"}:
                        self.assertTrue((control / "stop").exists())
                finally:
                    manager.close(wait_seconds=0)

    def test_changed_binding_stop_live_owner_or_auth_block_is_not_rebuilt(self):
        for change in ("prompt", "command", "instructions", "history", "stop", "owner", "auth", "symlink"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                run = self.fixture(Path(tmp).resolve())
                _, home, fingerprint = self.preparation(run)
                meta = home.parent
                if change == "prompt":
                    run.prompt = "different task"
                elif change in {"command", "instructions"}:
                    path = meta / ("command.json" if change == "command" else "developer_instructions.json")
                    path.write_text("{}" if change == "command" else '"changed instructions"')
                elif change == "history":
                    with (home / "sessions/rollout-thread-1.jsonl").open("a") as stream:
                        stream.write("changed\n")
                elif change == "stop":
                    (meta / "execution_stop").touch()
                elif change in {"owner", "auth"}:
                    path = meta / "execution_recovery.json"
                    value = json.loads(path.read_text())
                    if change == "owner":
                        value["native_pid"] = os.getpid()
                    else:
                        value["error"] = {"message": "Please sign in", "codexErrorInfo": "unauthorized"}
                    path.write_text(json.dumps(value))
                else:
                    original = meta / "command.json"
                    target = meta / "other-command.json"
                    original.rename(target)
                    original.symlink_to(target)
                with patch.object(app, "copy_codex_support_entries") as support:
                    with self.assertRaises((ValueError, OSError)):
                        app._prepare_retained_execution(run_root=Path(run.run_root), idx=1,
                            workspace=Path(run.results[1]["workspace_dir"]), prompt=run.prompt,
                            fingerprint=fingerprint, real_codex_home=home)
                    support.assert_not_called()
                self.assertFalse((Path(run.run_root) / "retry_history").exists())
                self.assertTrue((meta / "stdout.log").exists())

    def test_additional_recovery_never_constructs_a_new_private_home_or_cleans_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.fixture(Path(tmp).resolve())
            fingerprints = {idx: self.preparation(run, idx)[2] for idx in (1, 2)}
            args = SimpleNamespace(serial=False, max_parallel=None, recommend_by="reasoning_tokens",
                workspace_mode="isolated", codex_bin="codex", model=None, effort=None, fast=False)

            async def execute(**values):
                for idx in (1, 2):
                    self.assertEqual(values["command_by_agent"][idx], run.results[idx]["command"])
                    self.assertEqual(values["codex_home_by_agent"][idx], Path(run.results[idx]["codex_home"]))
                    command, home = values["initial_agent_preparer"](idx)
                    self.assertEqual(command, run.results[idx]["command"])
                    self.assertEqual(home, Path(run.results[idx]["codex_home"]))
                return []

            with patch.object(app, "read_codex_exec_resume_help", return_value=""), \
                 patch.object(app, "resolve_codex_reasoning_effort", return_value=None), \
                 patch.object(app, "copy_codex_support_entries"), patch.object(app, "scrub_codex_home_support_entries"), \
                 patch.object(app, "run_all_agents", side_effect=execute), \
                 patch.object(app, "AgentCodexSource", side_effect=AssertionError("history rebuilt")), \
                 patch.object(app, "_prepare_additional_agent", side_effect=AssertionError("workspace rebuilt")):
                app.run_additional_agents(args, run.prompt, [1, 2], Path(run.run_root), Path(run.workspace),
                    resume_session_id="original-parent", retry_indices={1, 2}, refresh_result_files=False,
                    retained_execution_fingerprints=fingerprints)

    def test_busy_batch_is_never_retired_to_recover_failed_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            manager, _, _ = self.manager(root, run)
            run.status = "running"
            run.worker_operation = "initial"
            try:
                with patch.object(plugin_runtime.FileSignal, "set") as stop, \
                     patch.object(plugin_runtime, "spawn_worker") as spawn:
                    with self.assertRaisesRegex(plugin_runtime.PluginRunError, "idle"):
                        manager.resume_agents_in_place(run.run_id, [1, 2])
                    stop.assert_not_called()
                    spawn.assert_not_called()
            finally:
                manager.close(wait_seconds=0)

    def test_stop_superseded_run_rejection_and_unfinished_checkpoint_are_not_bypassed(self):
        for change in ("stop", "agent-stop", "rejected", "superseded", "checkpoint", "queued"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                run = self.fixture(root)
                manager, checkpoint, path = self.manager(root, run)
                if change in {"stop", "agent-stop"}:
                    control = manager.control_dir / run.run_id
                    control.mkdir(parents=True)
                    (control / ("stop" if change == "stop" else "kill-agent-001")).touch()
                elif change == "rejected":
                    run.rejected_agents = [1]
                elif change == "superseded":
                    other = ManagedRun(run_id="pcr-20261015T000100Z-12345678", workspace=run.workspace,
                        prompt="later user request", config={}, created_at="9999-01-01T00:00:00Z")
                    manager._runs[other.run_id] = other
                elif change == "checkpoint":
                    path.write_text(json.dumps({**checkpoint, "active": True}))
                else:
                    requests = manager.state_dir / "workers/requests"
                    requests.mkdir()
                    (requests / f"{run.run_id}-pending.json").write_text("{}")
                try:
                    with patch.object(plugin_runtime, "validate_args"), \
                         patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
                         patch.object(plugin_runtime.FileSignal, "set") as stop, \
                         patch.object(plugin_runtime, "spawn_worker") as spawn:
                        with self.assertRaises(plugin_runtime.PluginRunError):
                            manager.resume_agents_in_place(run.run_id, [1, 2])
                        stop.assert_not_called()
                        spawn.assert_not_called()
                finally:
                    manager.close(wait_seconds=0)

    def test_scoped_maintenance_controller_does_not_run_global_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(plugin_runtime.PluginRunManager, "_run_maintenance_locked") as cleanup:
                manager = plugin_runtime.PluginRunManager(Path(tmp), run_maintenance=False)
                manager.close(wait_seconds=0)
                cleanup.assert_not_called()

    def test_busy_recovery_cli_never_constructs_a_mutating_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            run.status, run.worker_state = "running", "busy"
            state = root / "state"
            state.mkdir()
            (state / "runs.json").write_text(json.dumps({"version": 3, "runs": [run.to_dict()]}))
            script = Path(__file__).resolve().parents[1] / "scripts/resume_interrupted_agents.py"
            spec = importlib.util.spec_from_file_location("recovery_cli", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.object(sys, "argv", [str(script), "--state-dir", str(state), "--run-id", run.run_id,
                                          "--agents", "1", "2", "--execute"]), \
                 patch.object(module, "PluginRunManager", side_effect=AssertionError("busy batch modified")), \
                 patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(module.main(), 75)
            self.assertFalse(json.loads(output.getvalue())["submitted"])

    def test_idle_handoff_keeps_original_run_and_submits_only_fingerprint_bound_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            manager, checkpoint, path = self.manager(root, run)

            def retire(signal):
                self.assertEqual(signal.path, manager.control_dir / run.run_id / "shutdown")
                manager._events.append(run.run_id, {"type": "plugin_worker_stopped", "pid": run.worker_pid,
                                                  "reason": "shutdown", "exit_code": 0})
                path.write_text(json.dumps({**checkpoint, "alive": False, "stop_reason": "shutdown"}))

            try:
                with patch.object(plugin_runtime.FileSignal, "set", retire), \
                     patch.object(plugin_runtime, "validate_args"), \
                     patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
                     patch.object(plugin_runtime, "spawn_worker", return_value=(SimpleNamespace(pid=54321, poll=lambda: None), "new-op")) as spawn:
                    result = manager.resume_agents_in_place(run.run_id, [1, 2])
                self.assertEqual(result["status"], "running")
                self.assertEqual(run.worker_pid, 54321)
                self.assertEqual(result["recovery_operation_id"], run.worker_operation_id)
                spawn.assert_called_once()
                payload = spawn.call_args.args[3]
                self.assertEqual(payload["indices"], [1, 2])
                self.assertEqual(set(payload["retained_execution_fingerprints"]), {"1", "2"})
                self.assertEqual(payload["run"]["prompt"], "original expensive task")
                self.assertEqual(payload["run"]["run_root"], run.run_root)
                self.assertEqual(payload["run"]["config"], run.config)
                self.assertTrue(payload["refresh_synthesis"])
                self.assertNotIn("retained_startup_fingerprint", payload)
                self.assertTrue((Path(run.run_root) / "workspaces/agent_001/trained.bin").is_file())
            finally:
                manager._worker_processes.clear()
                manager.close(wait_seconds=0)

    def test_user_stop_during_idle_worker_retirement_is_never_cleared_or_dispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            manager, checkpoint, path = self.manager(root, run)
            stop = manager.control_dir / run.run_id / "stop"

            def retire(signal):
                stop.parent.mkdir(parents=True, exist_ok=True)
                stop.touch()
                path.write_text(json.dumps({**checkpoint, "alive": False, "stop_reason": "shutdown"}))

            try:
                with patch.object(plugin_runtime.FileSignal, "set", retire), \
                     patch.object(plugin_runtime, "validate_args"), \
                     patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
                     patch.object(plugin_runtime, "spawn_worker") as spawn:
                    with self.assertRaisesRegex(plugin_runtime.PluginRunError, "Retained execution dispatch"):
                        manager.resume_agents_in_place(run.run_id, [1, 2])
                    spawn.assert_not_called()
                self.assertTrue(stop.exists())
                self.assertEqual({idx: value["status"] for idx, value in run.results.items()}, {1: "failed", 2: "failed"})
            finally:
                manager.close(wait_seconds=0)

    def test_invalid_or_wrong_stage_worker_recovery_request_never_starts_an_agent(self):
        for change in ({"operation": "initial"}, {"retry_indices": []}, {"agent_role": "synthesis"},
                       {"retained_execution_fingerprints": {"2": "a" * 64}},
                       {"retained_execution_fingerprints": {"1": "bad"}}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                run = self.fixture(root)
                request = {"protocol": WORKER_PROTOCOL_VERSION, "run": run.to_dict(), "operation": "retry",
                           "indices": [1], "retry_indices": [1], "agent_role": "candidate",
                           "retained_execution_fingerprints": {"1": "a" * 64}, **change}
                path = root / "request.json"
                path.write_text(json.dumps(request))
                with patch.object(worker, "run_once") as initial, patch.object(worker, "run_additional_agents") as retry:
                    with self.assertRaisesRegex(RuntimeError, "Retained execution recovery"):
                        worker._execute_request(root / "state", path)
                    initial.assert_not_called()
                    retry.assert_not_called()

    def test_old_worker_handoff_really_resumes_both_private_threads_without_replaying_the_task(self):
        self._assert_native_recovery()

    def test_dead_busy_worker_recovers_failed_and_accepted_candidates_without_replaying_original_input(self):
        self._assert_native_recovery(orphaned=True)

    def test_external_shutdown_resumes_cancelled_candidates_without_replaying_input(self):
        self._assert_native_recovery(external_role="candidate")

    def test_external_shutdown_resumes_cancelled_synthesis_without_replaying_input(self):
        self._assert_native_recovery(external_role="synthesis")

    def test_configuration_recovery_submits_original_synthesis_input_once_in_retained_home(self):
        self._assert_native_recovery(configuration=True)

    def test_normal_candidate_retry_uses_the_idle_worker_and_resumes_the_original_execution(self):
        self._assert_native_recovery(normal_role="candidate")

    def test_normal_synthesis_retry_preserves_its_original_context_and_other_results(self):
        self._assert_native_recovery(normal_role="synthesis")

    def test_synthesis_recovery_reports_actual_success_count(self):
        self._assert_native_recovery(external_role="synthesis", fail_second=True)

    def test_configuration_failure_with_submission_or_tool_evidence_is_not_reset(self):
        for change in ("thread_id", "turn_id", "client_message_id", "tool"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                run = self.fixture(Path(tmp).resolve())
                meta = Path(run.results[1]["meta_dir"])
                receipt = json.loads((meta / "execution_recovery.json").read_text())
                error = {"code": -32600, "message": "failed to load configuration: duplicate key"}
                receipt.update(stage="blocked", thread_id="", turn_id="", client_message_id="", error=error)
                events = [{"type": "error", "error": error}]
                if change == "tool":
                    events.insert(0, {"type": "item.started", "item": {"type": "command_execution"}})
                else:
                    receipt[change] = "accepted"
                (meta / "stdout.log").write_text("\n".join(map(json.dumps, events)))
                (meta / "execution_recovery.json").write_text(json.dumps(receipt))
                self.assertFalse(app.configuration_failed_before_input(meta, receipt))
                with self.assertRaises(ValueError):
                    self.preparation(run)

    def external_shutdown(self, manager, run, checkpoint, path):
        old_pid = 999999999
        checkpoint.update(pid=old_pid, alive=False, active=False, stop_reason="shutdown", exit_code=130)
        path.write_text(json.dumps(checkpoint))
        run.status, run.completion_reason, run.worker_state, run.worker_pid = "cancelled", "cancelled", "stopped", None
        control = manager.control_dir / run.run_id
        control.mkdir(parents=True, exist_ok=True)
        for signal in ("stop", "shutdown"):
            (control / signal).write_text(str(old_pid) + "\n")
        manager._append_event_locked(run, {"type": "batch_finished", "cancelled": True})
        manager._append_event_locked(run, {"type": "plugin_worker_stopped", "pid": old_pid, "reason": "shutdown"})
        for idx, result in run.results.items():
            result["status"] = run.agent_statuses[idx] = "cancelled"
            meta = Path(result["meta_dir"])
            (meta / "status.json").write_text(json.dumps(result))
            (meta / "execution_stop").write_text("cancelled")
            receipt = json.loads((meta / "execution_recovery.json").read_text())
            receipt.update(stage="stopped", error=None)
            (meta / "execution_recovery.json").write_text(json.dumps(receipt))
        manager._save_state_locked()

    def test_external_shutdown_never_revives_user_stop_or_live_owner(self):
        for change in ("user-stop", "agent-stop", "signal-owner", "alive", "successful"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                run = self.fixture(root)
                manager, checkpoint, path = self.manager(root, run)
                try:
                    self.external_shutdown(manager, run, checkpoint, path)
                    control = manager.control_dir / run.run_id
                    if change in ("user-stop", "agent-stop"):
                        manager._append_event_locked(run, {"type": "run_stop_requested" if change == "user-stop" else "agent_stop_requested", "idx": 1})
                    elif change == "signal-owner":
                        (control / "stop").write_text(str(os.getpid()))
                    elif change == "alive":
                        checkpoint["alive"] = True
                        path.write_text(json.dumps(checkpoint))
                    else:
                        run.results[1]["status"] = "success"
                    before = (control / "stop").read_bytes()
                    with patch.object(manager, "_start_additional_batch") as start:
                        with self.assertRaises(plugin_runtime.PluginRunError):
                            manager.resume_external_shutdown_agents(run.run_id, [1, 2])
                        start.assert_not_called()
                    self.assertEqual((control / "stop").read_bytes(), before)
                    self.assertTrue((Path(run.results[1]["meta_dir"]) / "execution_stop").exists())
                finally:
                    manager.close(wait_seconds=0)

    def _assert_native_recovery(self, orphaned=False, external_role=None, configuration=False, fail_second=False, normal_role=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run = self.fixture(root)
            run.config["synthesis_agents"] = 0
            executable = root / "codex"
            executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--help" in sys.argv:
    print("Usage: codex exec resume --json --output-last-message --model --config --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check")
    raise SystemExit(0)
if "--version" in sys.argv:
    print("codex-cli 0.159.2")
    raise SystemExit(0)
assert sys.argv[1] == "app-server"
meta = Path(os.environ["CODEX_HOME"]).parent
saved = json.loads((meta / "execution_recovery.json").read_text())
thread_id = saved["thread_id"] or json.loads((meta / "command.json").read_text())["command"][-2]
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method, key = request["method"], request.get("id")
    if method == "initialized":
        continue
    if method == "initialize":
        result = {"userAgent": "codex/0.159.2"}
    elif method == "thread/resume":
        assert request["params"]["threadId"] == thread_id
        result = {"thread": {"id": thread_id, "cwd": os.getcwd()}}
    elif method == "thread/turns/list":
        result = {"data": [{"id": saved["turn_id"],
            "status": "failed" if saved["error"] else "interrupted", "error": saved["error"]}]}
    elif method == "thread/items/list":
        result = {"data": [{"turnId": saved["turn_id"], "item": {
            "id": "original-job", "type": "commandExecution", "status": "completed"}}], "nextCursor": None}
    elif method == "turn/start":
        params = request["params"]
        assert params["threadId"] == thread_id
        expected = "original expensive task" if saved["stage"] == "ready" else "Continue the unfinished task from its saved progress, without restarting completed work."
        assert params["input"][0]["text"] == expected
        with (meta / "resume-starts.jsonl").open("a") as output:
            output.write(json.dumps(params) + "\\n")
        turn = {"id": "recovered-turn", "status": "completed", "error": None}
        if (meta / "fail-on-resume").exists():
            turn.update(status="failed", error={"message": "Context window exceeded", "codexErrorInfo": "contextWindowExceeded"})
        emit({"method": "turn/started", "params": {"threadId": thread_id, "turn": turn}})
        emit({"id": key, "result": {"turn": turn}})
        emit({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn["id"],
            "item": {"type": "agentMessage", "id": "answer", "phase": "final_answer", "text": "Resumed " + thread_id}}})
        emit({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
        continue
    else:
        raise AssertionError(method)
    emit({"id": key, "result": result})
''')
            executable.chmod(0o755)
            before = {}
            for idx in (1, 2):
                result = run.results[idx]
                meta, home = Path(result["meta_dir"]), Path(result["codex_home"])
                recorded = json.loads((meta / "command.json").read_text())
                if external_role or configuration or normal_role:
                    result["role"] = recorded["role"] = run.agent_roles[idx] = external_role or normal_role or "synthesis"
                recorded["command"][0] = str(executable)
                result["command"] = recorded["command"]
                (meta / "command.json").write_text(json.dumps(recorded))
                (meta / "status.json").write_text(json.dumps(result))
                receipt = json.loads((meta / "execution_recovery.json").read_text())
                receipt["identity"] = hashlib.sha256(json.dumps([result["command"], result["workspace_dir"],
                    result["codex_home"], run.prompt, "original permissions"], ensure_ascii=False).encode()).hexdigest()
                (meta / "execution_recovery.json").write_text(json.dumps(receipt))
                before[idx] = [(path, path.stat().st_ino, path.read_bytes()) for path in (
                    Path(result["workspace_dir"]) / "trained.bin", home / "state_5.sqlite",
                    home / f"sessions/rollout-thread-{idx}.jsonl")]
            source_home = root / "source-home"
            source_home.mkdir()
            if fail_second:
                (Path(run.results[2]["meta_dir"]) / "fail-on-resume").touch()
            with patch.dict(os.environ, {"CODEX_HOME": str(source_home)}):
                manager, checkpoint, checkpoint_path = self.manager(root, run)
                process = None
                server = None
                controller = None
                old_operation_id = run.run_id + "-old-executed-operation"
                if configuration:
                    checkpoint.update(pid=999999999, alive=False, active=False, stop_reason="shutdown", exit_code=0)
                    checkpoint_path.write_text(json.dumps(checkpoint))
                    run.worker_state, run.worker_pid = "stopped", None
                    for idx, result in run.results.items():
                        meta = Path(result["meta_dir"])
                        result["codex_thread_id"] = None
                        (meta / "status.json").write_text(json.dumps(result))
                        receipt = json.loads((meta / "execution_recovery.json").read_text())
                        error = {"code": -32600, "message": "failed to load configuration: config.toml:12: duplicate key"}
                        receipt.update(stage="blocked", thread_id="", turn_id="", client_message_id="", error=error)
                        (meta / "execution_recovery.json").write_text(json.dumps(receipt))
                        (meta / "stdout.log").write_text(json.dumps({"type": "error", "error": error}) + "\n")
                    manager._save_state_locked()
                elif external_role:
                    self.external_shutdown(manager, run, checkpoint, checkpoint_path)
                elif orphaned:
                    old_pid = 999999999
                    checkpoint.update(pid=old_pid, active=True, operation="initial", operation_id=old_operation_id)
                    checkpoint_path.write_text(json.dumps(checkpoint))
                    run.status, run.worker_state, run.worker_pid = "interrupted", "crashed", None
                    run.agent_statuses[1] = "interrupted"
                    pending = run.results.pop(1)
                    meta = Path(pending["meta_dir"])
                    (meta / "status.json").unlink()
                    receipt = json.loads((meta / "execution_recovery.json").read_text())
                    receipt.update(stage="accepted", error=None)
                    (meta / "execution_recovery.json").write_text(json.dumps(receipt))
                    requests = manager.state_dir / "workers/requests"
                    requests.mkdir()
                    original_request = {"protocol": WORKER_PROTOCOL_VERSION,
                        "operation": "initial", "operation_id": old_operation_id, "run": run.to_dict()}
                    original_request["run"]["run_root"] = ""
                    (requests / f"{old_operation_id}.json").write_text(json.dumps(original_request))

                def retire(signal):
                    manager._events.append(run.run_id, {"type": "plugin_worker_stopped", "pid": run.worker_pid,
                                                       "reason": "shutdown", "exit_code": 0})
                    checkpoint_path.write_text(json.dumps({**checkpoint, "alive": False, "stop_reason": "shutdown"}))

                try:
                    if normal_role:
                        original_second = dict(run.results[2])
                        (Path(run.run_root) / "synthesis_context.md").write_text("original synthesis inputs")
                        worker_python = os.environ.get("PCR_RETRY_WORKER_PYTHON", sys.executable)
                        process = subprocess.Popen([worker_python, "-m", "parallel_codex_runner_core.plugin.worker",
                            "--state-dir", str(manager.state_dir), "--run-id", run.run_id],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            if json.loads(checkpoint_path.read_text()).get("pid") == process.pid:
                                break
                            time.sleep(0.01)
                        self.assertIsNone(process.poll())
                        run.worker_pid = process.pid
                        manager._worker_processes[run.run_id] = process
                        manager._save_state_locked()
                        self.assertTrue(manager.get_run(run.run_id)["agents"][0]["can_retry"])
                        controller = RemoteController(state_dir=root / "remote", manager=manager)
                        server = PcrdServer(root / "retry.sock", controller)
                        server_thread = server.start_thread()
                        client = PcrdClient(root / "retry.sock", timeout=5)
                        result = client.request("runs.retry_agent", {"run_id": run.run_id, "agent": 1},
                            command_id="original-retry-1")
                        replay = client.request("runs.retry_agent", {"run_id": run.run_id, "agent": 1},
                            command_id="original-retry-1")
                        self.assertTrue(replay["command_replayed"])
                        self.assertEqual(result["worker_pid"], process.pid)
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            current = manager.get_run(run.run_id)
                            if current["status"] == "completed":
                                break
                            time.sleep(0.05)
                        self.assertEqual(run.results[1]["status"], "success", run.results[1])
                        self.assertEqual(run.results[2], original_second)
                        self.assertFalse((Path(run.results[2]["meta_dir"]) / "resume-starts.jsonl").exists())
                        self.assertEqual((Path(run.run_root) / "synthesis_context.md").read_text(), "original synthesis inputs")
                        result = client.request("runs.retry_agent", {"run_id": run.run_id, "agent": 2})
                    elif configuration:
                        result = manager.recover_configuration_failures(run.run_id, [1, 2])
                    elif external_role:
                        result = manager.resume_external_shutdown_agents(run.run_id, [1, 2])
                    elif orphaned:
                        save_state = manager._save_state_locked
                        unpublished_checks = []

                        def delay_publication(**kwargs):
                            if run.worker_pid is not None and run.worker_state == "busy":
                                time.sleep(0.3)
                                # The real replacement process exists, but no
                                # native execution/event can precede its commit.
                                self.assertEqual(json.loads(checkpoint_path.read_text())["pid"], old_pid)
                                self.assertFalse(list(Path(run.run_root).glob("meta/*/resume-starts.jsonl")))
                                unpublished_checks.append(True)
                            return save_state(**kwargs)

                        with patch.object(manager, "_save_state_locked", side_effect=delay_publication):
                            result = manager.resume_interrupted_batch(run.run_id, [1, 2],
                                old_pid=old_pid, old_operation_id=old_operation_id)
                        self.assertEqual(unpublished_checks, [True])
                        self.assertTrue((manager.state_dir / "workers/retired" / old_operation_id
                            / f"{old_operation_id}.json").is_file())
                    else:
                        with patch.object(plugin_runtime.FileSignal, "set", retire):
                            result = manager.resume_agents_in_place(run.run_id, [1, 2])
                    process = manager._worker_processes[run.run_id]
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        current = manager.get_run(run.run_id)
                        if current["status"] == "completed":
                            break
                        time.sleep(0.05)
                    self.assertEqual(current["status"], "completed", current)
                    self.assertEqual({idx: value["status"] for idx, value in run.results.items()}, {1: "success", 2: "failed" if fail_second else "success"},
                        {idx: (value.get("error"), value.get("stderr_tail"), value.get("stdout_tail"))
                         for idx, value in run.results.items()})
                    if external_role == "synthesis":
                        events = [json.loads(line) for line in manager._events.event_path(run.run_id).read_text().splitlines()]
                        finished = [event for event in events if event.get("type") == "synthesis_finished"]
                        self.assertEqual(finished[-1]["success"], 1 if fail_second else 2)
                    for idx in (1, 2):
                        meta = Path(run.run_root) / "meta" / f"agent_{idx:03d}"
                        starts = (meta / "resume-starts.jsonl").read_text().splitlines()
                        self.assertEqual(len(starts), 1)
                        self.assertEqual(json.loads(starts[0])["threadId"], f"thread-{idx}")
                        self.assertEqual((meta / "final_message.md").read_text(), f"Resumed thread-{idx}")
                        if not configuration:
                            self.assertTrue((meta / "stdout.log").read_text().startswith("all original tool events\n"))
                        for path, inode, contents in before[idx]:
                            self.assertEqual((path.stat().st_ino, path.read_bytes()), (inode, contents))
                    if compatible_python := os.environ.get("PCR_RECOVERY_COMPAT_PYTHON"):
                        check = subprocess.check_output([compatible_python, "-I", "-c", '''
import json,sys
from pathlib import Path
from parallel_codex_runner_core.plugin_runtime import PluginRunManager
manager = PluginRunManager(Path(sys.argv[1]), detached_workers=True)
try:
    value = manager.get_run(sys.argv[2])
    print(json.dumps({"status": value["status"], "available": value["worker_available"],
        "worker_pid": value["worker_pid"], "agents": [agent["status"] for agent in value["agents"]]}))
finally:
    manager.close(wait_seconds=0)
''', str(manager.state_dir), run.run_id], text=True)
                        old_reader = json.loads(check)
                        self.assertEqual(old_reader, {"status": "completed", "available": True,
                            "worker_pid": process.pid, "agents": ["success", "failed" if fail_second else "success"]})
                finally:
                    if server is not None:
                        server.shutdown()
                        server.server_close()
                        server_thread.join(timeout=5)
                    if controller is not None:
                        controller.close()
                    if process is None:
                        process = manager._worker_processes.get(run.run_id)
                    if process is not None:
                        from parallel_codex_runner_core.plugin.lifecycle import FileSignal
                        FileSignal(manager.control_dir / run.run_id / "shutdown").set()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            process.wait(timeout=5)
                        if process.stderr:
                            process.stderr.close()
                    manager._worker_processes.clear()
                    manager.close(wait_seconds=0)

    def test_unpublished_worker_never_emits_events_when_publisher_dies_or_user_stops(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                run_id = "pcr-20261015T000000Z-12345678"
                if stopped:
                    stop = root / "control" / run_id / "stop"
                    stop.parent.mkdir(parents=True)
                    stop.touch()
                with patch.dict(os.environ, {"PCR_WORKER_PUBLICATION_OPERATION_ID": run_id + "-publish",
                    "PCR_WORKER_PUBLICATION_PARENT_PID": "999999999"}), \
                     patch.object(worker, "preload_plugin_worker_runtime"):
                    self.assertEqual(worker.serve_run(root, run_id), 130 if stopped else 1)
                self.assertFalse((root / "events").exists())
                self.assertFalse((root / "workers").exists())
