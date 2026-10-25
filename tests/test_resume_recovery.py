import json
import os
import tempfile
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from parallel_codex_runner_core import app, plugin_runtime
from parallel_codex_runner_core.plugin.lifecycle import WORKER_PROTOCOL_VERSION
from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.plugin import worker
from parallel_codex_runner_core.plugin.lifecycle import FileSignal, read_json


class RetainedResumeRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        workspace = root / "workspace"
        workspace.mkdir()
        run_root = root / "runs" / "20261006_001315"
        meta = run_root / "meta" / "agent_001"
        home = meta / "codex_home"
        sessions = home / "sessions"
        sessions.mkdir(parents=True)
        rollout = sessions / "rollout-original-session.jsonl"
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "original-session"}}) + "\noriginal history\n")
        (home / "state_5.sqlite").write_bytes(b"original SQLite")
        prompt = "original question"
        (run_root / "prompt.txt").write_text(prompt)
        command = ["codex", "exec", "resume", "--json", "-o", str(meta / "final_message.md"), "original-session", "-"]
        error = json.dumps({"type": "error", "message": "Codex thread/resume did not respond within 90 seconds."})
        result = {
            "idx": 1, "role": "candidate", "workspace_dir": str(workspace),
            "meta_dir": str(meta), "codex_home": str(home),
            "stdout_log": str(meta / "stdout.log"), "stderr_log": str(meta / "stderr.log"),
            "final_message": str(meta / "final_message.md"), "command": command,
            "returncode": 1, "status": "failed", "seconds": 95,
            "codex_thread_id": None, "reasoning_tokens": None, "stdout_tail": error,
        }
        (meta / "status.json").write_text(json.dumps(result))
        (meta / "command.json").write_text(json.dumps({"idx": 1, "role": "candidate", "cwd": str(workspace), "codex_home": str(home), "command": command}))
        (meta / "stdout.log").write_text(error + "\n")
        (meta / "stderr.log").write_text("")
        run = ManagedRun(
            run_id="pcr-20261005T161214Z-e07f19f2", prompt=prompt, workspace=str(workspace),
            config={"num_agents": 1, "synthesis_agents": 0, "workspace_mode": "source",
                    "resume_session_id": "original-session", "run_base": str(run_root.parent)},
            status="completed", run_root=str(run_root), agent_statuses={1: "failed"},
            agent_roles={1: "candidate"}, results={1: result},
            worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="idle", worker_pid=os.getpid(),
            artifact_token="recovery-fixture",
        )
        return run, meta, home, rollout

    def test_recovery_keeps_original_history_database_command_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run, meta, home, rollout = self.fixture(root)
            command, retained, fingerprint = app.retained_resume_preparation(
                Path(run.run_root), 1, Path(run.workspace), "original-session", run.prompt)
            history_stat = rollout.stat()
            database_stat = (home / "state_5.sqlite").stat()
            source = root / "real_codex_home"
            (source / "sessions").mkdir(parents=True)
            (source / "sessions/newer.jsonl").write_text("newer conversation must not replace original")
            (source / "state_5.sqlite").write_bytes(b"newer SQLite")
            (source / "auth.json").write_text("test credentials")
            with patch.dict(os.environ, {"PCR_CODEX_TRANSPORT": "app-server"}), \
                 patch.object(app, "read_effective_codex_developer_instructions",
                    side_effect=AssertionError("Retained recovery must not probe config")):
                prepared = app._prepare_retained_resume(
                    run_root=Path(run.run_root), idx=1, workspace=Path(run.workspace),
                    resume_session_id="original-session", prompt=run.prompt,
                    fingerprint=fingerprint, real_codex_home=source,
                )
            self.assertEqual(prepared, (command, retained))
            instructions = json.loads((meta / "developer_instructions.json").read_text())
            self.assertIn(str(Path(run.workspace)), instructions)
            self.assertIn(json.dumps({"run": Path(run.run_root).name, "agent": 1}), instructions)
            self.assertNotIn(".pcr/", instructions)
            self.assertIn("PCR custom tool lifecycle requirement:", instructions)
            self.assertEqual((rollout.stat().st_ino, rollout.read_text()), (history_stat.st_ino, json.dumps({"type": "session_meta", "payload": {"id": "original-session"}}) + "\noriginal history\n"))
            self.assertEqual((home / "state_5.sqlite").stat().st_ino, database_stat.st_ino)
            self.assertEqual((home / "state_5.sqlite").read_bytes(), b"original SQLite")
            self.assertFalse((home / "sessions/newer.jsonl").exists())
            self.assertEqual((home / "auth.json").read_text(), "test credentials")
            self.assertEqual(len(list((Path(run.run_root) / "retry_history/agent_001").glob("*/stdout.log"))), 1)
            self.assertFalse((meta / "stdout.log").exists())

    def test_recovery_rejects_unknown_submission_changed_binding_and_missing_history(self):
        for change in ("turn_started", "turn_timeout", "prompt", "history", "command", "final"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                run, meta, home, rollout = self.fixture(Path(tmp).resolve())
                if change == "turn_started":
                    with (meta / "stdout.log").open("a") as stream:
                        stream.write('{"type":"turn.started"}\n')
                elif change == "turn_timeout":
                    (meta / "stdout.log").write_text('{"type":"error","message":"Codex turn/start did not respond within 90 seconds."}\n')
                elif change == "prompt":
                    (Path(run.run_root) / "prompt.txt").write_text("changed question")
                elif change == "history":
                    rollout.unlink()
                elif change == "command":
                    data = json.loads((meta / "command.json").read_text())
                    data["command"][-2] = "newer-session"
                    (meta / "command.json").write_text(json.dumps(data))
                elif change == "final":
                    (meta / "final_message.md").write_text("already executed")
                with self.assertRaises((ValueError, OSError)):
                    app.retained_resume_preparation(Path(run.run_root), 1, Path(run.workspace), "original-session", run.prompt)
                self.assertTrue((meta / "status.json").exists())

    def test_change_after_preflight_never_archives_or_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, meta, home, rollout = self.fixture(Path(tmp).resolve())
            _, _, fingerprint = app.retained_resume_preparation(Path(run.run_root), 1, Path(run.workspace), "original-session", run.prompt)
            rollout.write_text(rollout.read_text() + "changed\n")
            with self.assertRaisesRegex(ValueError, "changed after"):
                app._prepare_retained_resume(
                    run_root=Path(run.run_root), idx=1, workspace=Path(run.workspace),
                    resume_session_id="original-session", prompt=run.prompt,
                    fingerprint=fingerprint, real_codex_home=Path(tmp) / "other",
                )
            self.assertTrue((meta / "status.json").exists())

    def test_guidance_changes_after_preflight_never_archive_retained_startup(self):
        for change in ("added", "changed", "removed"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                run, meta, _, _ = self.fixture(root)
                path = meta / "developer_instructions.json"
                if change != "added":
                    path.write_text(json.dumps("Original PCR guidance."))
                _, _, fingerprint = app.retained_resume_preparation(
                    Path(run.run_root), 1, Path(run.workspace), "original-session", run.prompt)
                if change == "removed":
                    path.unlink()
                else:
                    path.write_text(json.dumps("Changed PCR guidance."))
                with self.assertRaisesRegex(ValueError, "changed after"):
                    app._prepare_retained_resume(
                        run_root=Path(run.run_root), idx=1, workspace=Path(run.workspace),
                        resume_session_id="original-session", prompt=run.prompt,
                        fingerprint=fingerprint, real_codex_home=root / "other")
                self.assertTrue((meta / "status.json").exists())
                self.assertFalse((Path(run.run_root) / "retry_history").exists())

    def test_retained_guidance_must_be_valid_and_not_a_symlink(self):
        for metadata in ("{broken", "[]", '""', "symlink", "dangling"):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                run, meta, _, _ = self.fixture(root)
                path = meta / "developer_instructions.json"
                if metadata in {"symlink", "dangling"}:
                    target = root / "outside-guidance.json"
                    if metadata == "symlink":
                        target.write_text(json.dumps("Other guidance."))
                    path.symlink_to(target)
                else:
                    path.write_text(metadata)
                with self.assertRaises(ValueError):
                    app.retained_resume_preparation(
                        Path(run.run_root), 1, Path(run.workspace), "original-session", run.prompt)
                self.assertTrue((meta / "status.json").exists())

    def manager_fixture(self, root):
        run, meta, home, rollout = self.fixture(root)
        manager = plugin_runtime.PluginRunManager(root / "state", detached_workers=True)
        manager._runs[run.run_id] = run
        manager._write_run_marker_locked(run)
        checkpoint = {
            "run_id": run.run_id, "pid": run.worker_pid, "protocol": WORKER_PROTOCOL_VERSION,
            "alive": True, "active": False, "operation": "", "operation_id": "",
            "last_operation": "initial", "finished_at": "2026-10-05T16:18:05Z", "exit_code": 2,
        }
        checkpoint_path = manager.state_dir / "workers" / f"{run.run_id}.json"
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(checkpoint))
        return manager, run, checkpoint, checkpoint_path

    def test_explicit_recovery_retires_only_idle_worker_then_spawns_current_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, run, checkpoint, path = self.manager_fixture(Path(tmp).resolve())

            def retire(signal):
                self.assertEqual(signal.path.name, "shutdown")
                self.assertEqual(signal.path.parent.name, run.run_id)
                manager._events.append(run.run_id, {
                    "type": "plugin_worker_stopped", "pid": run.worker_pid,
                    "reason": "shutdown", "exit_code": 2,
                })
                path.write_text(json.dumps({**checkpoint, "alive": False, "stop_reason": "shutdown"}))

            try:
                with patch.object(plugin_runtime.FileSignal, "set", retire), \
                     patch.object(plugin_runtime, "validate_args"), \
                     patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
                     patch.object(plugin_runtime, "spawn_worker", return_value=(SimpleNamespace(pid=54321, poll=lambda: None), "new-op")) as spawn, \
                     patch.object(plugin_runtime, "enqueue_worker_operation") as enqueue:
                    response = manager.retry_agent(run.run_id, 1, refresh_startup_runtime=True)
                self.assertEqual(response["status"], "running")
                self.assertEqual(run.worker_pid, 54321)
                self.assertEqual(run.prompt, "original question")
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args[2], "retry")
                queued = spawn.call_args.args[3]
                self.assertEqual(queued["run"]["run_root"], run.run_root)
                self.assertTrue(queued["retained_startup_fingerprint"])
                self.assertEqual(queued["run"]["config"]["resume_session_id"], "original-session")
                self.assertEqual(queued["indices"], [1])
                enqueue.assert_not_called()
            finally:
                manager._worker_processes.clear()
                manager.close(wait_seconds=0)

    def test_retained_recovery_with_wrong_operation_fails_before_any_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            run, _, _, _ = self.fixture(root)
            request = root / "request.json"
            request.write_text(json.dumps({"protocol": WORKER_PROTOCOL_VERSION, "run": run.to_dict(),
                "operation": "initial", "indices": [1], "retry_indices": [1],
                "retained_startup_fingerprint": "a" * 64}))
            with patch.object(worker, "run_once") as initial, patch.object(worker, "run_additional_agents") as retry:
                with self.assertRaisesRegex(RuntimeError, "cannot create an initial run"):
                    worker._execute_request(root / "state", request)
            initial.assert_not_called()
            retry.assert_not_called()

    def test_real_detached_handoff_reuses_private_history_and_original_run_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manager, run, checkpoint, checkpoint_path = self.manager_fixture(root)
            original_root = run.run_root
            home = Path(original_root) / "meta/agent_001/codex_home"
            rollout = home / "sessions/rollout-original-session.jsonl"
            original_inode = rollout.stat().st_ino
            database_inode = (home / "state_5.sqlite").stat().st_ino
            executable = root / "codex"
            executable.write_text('''#!/usr/bin/env python3
import json, sys
if "--help" in sys.argv:
    print("Usage: codex exec resume [OPTIONS] [SESSION_ID] --json --output-last-message --model --config --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check")
    raise SystemExit(0)
if "--version" in sys.argv:
    print("protocol fixture without native cache")
    raise SystemExit(0)
assert sys.argv[1] == "app-server"
def send(v): print(json.dumps(v), flush=True)
for line in sys.stdin:
    m=json.loads(line)
    if m.get("method")=="initialize": send({"id":m["id"],"result":{"userAgent":"codex_cli_rs/0.159.2"}})
    if m.get("method")=="thread/resume": send({"id":m["id"],"result":{"thread":{"id":"original-session"}}})
    if m.get("method")=="turn/start":
        assert m["params"]["input"][0]["text"]=="original question"
        assert m["params"]["additionalContext"]["parallel_codex_runner"]["kind"]=="application"
        assert "PCR custom tool lifecycle requirement:" in m["params"]["additionalContext"]["parallel_codex_runner"]["value"]
        send({"id":m["id"],"result":{"turn":{"id":"original-turn"}}})
        send({"method":"item/completed","params":{"threadId":"original-session","turnId":"original-turn","item":{"type":"agentMessage","id":"answer","text":"done","phase":"final_answer"}}})
        send({"method":"turn/completed","params":{"threadId":"original-session","turn":{"id":"original-turn","status":"completed"}}})
        break
''')
            executable.chmod(0o755)
            run.config["codex_bin"] = str(executable)
            for name in ("status.json", "command.json"):
                path = home.parent / name
                data = json.loads(path.read_text())
                data["command"][0] = str(executable)
                path.write_text(json.dumps(data))
            run.results[1]["command"][0] = str(executable)
            source_home = root / "source-home"
            source_home.mkdir()
            environment = dict(os.environ, CODEX_HOME=str(source_home), PCR_INTERACTIVE_QA_AUTHORIZED="0",
                               PYTHONPATH=str(Path(worker.__file__).resolve().parents[2]))
            processes = []
            try:
                manager._save_state_locked()
                old = subprocess.Popen([sys.executable, "-m", "parallel_codex_runner_core.plugin.worker",
                    "--state-dir", str(manager.state_dir), "--run-id", run.run_id], env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(old)
                run.worker_pid = old.pid
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if read_json(checkpoint_path).get("pid") == old.pid:
                        break
                    time.sleep(0.05)
                with patch.dict(os.environ, environment):
                    response = manager.retry_agent(run.run_id, 1, refresh_startup_runtime=True)
                new = manager._worker_processes[run.run_id]
                processes.append(new)
                self.assertNotEqual(response["worker_pid"], old.pid)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    detail = manager.get_run(run.run_id)
                    if detail["agents"][0]["status"] == "success" and not detail["worker_active"]:
                        break
                    time.sleep(0.05)
                else:
                    self.fail(str(detail))
                self.assertEqual(detail["run_root"], original_root)
                self.assertEqual(rollout.stat().st_ino, original_inode)
                self.assertEqual((home / "state_5.sqlite").stat().st_ino, database_inode)
                self.assertEqual((home.parent / "final_message.md").read_text(), "done")
                events = [json.loads(line) for line in (home.parent / "stdout.log").read_text().splitlines()]
                self.assertEqual(sum(event.get("type") == "turn.started" for event in events), 1)
                self.assertEqual(len(list((root / "runs").iterdir())), 1)
                self.assertEqual(len(list((Path(original_root) / "retry_history/agent_001").glob("*/stdout.log"))), 1)
            finally:
                FileSignal(manager.control_dir / run.run_id / "shutdown").set()
                for process in processes:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
                manager._worker_processes.clear()
                manager.close(wait_seconds=0)

    def test_active_or_ambiguous_worker_is_never_retired_or_replaced(self):
        for change in ("active", "queued", "crashed", "unknown_submission"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                manager, run, checkpoint, path = self.manager_fixture(Path(tmp).resolve())
                if change == "active":
                    checkpoint["active"] = True
                    path.write_text(json.dumps(checkpoint))
                elif change == "queued":
                    requests = manager.state_dir / "workers/requests"
                    requests.mkdir(parents=True, exist_ok=True)
                    (requests / f"{run.run_id}-queued.json").write_text("{}")
                elif change == "crashed":
                    run.worker_state = "crashed"
                else:
                    (Path(run.run_root) / "meta/agent_001/stdout.log").write_text('{"type":"turn.started"}\n')
                try:
                    with patch.object(plugin_runtime.FileSignal, "set") as stop, \
                         patch.object(plugin_runtime, "spawn_worker") as spawn:
                        with self.assertRaises(plugin_runtime.PluginRunError):
                            manager.retry_agent(run.run_id, 1, refresh_startup_runtime=True)
                    stop.assert_not_called()
                    spawn.assert_not_called()
                    self.assertEqual(run.results[1]["status"], "failed")
                finally:
                    manager.close(wait_seconds=0)
