"""Exercise one original continuation through a legacy failure and lost receipt."""

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest import mock

from parallel_codex_runner_core.plugin.lifecycle import FileSignal
from parallel_codex_runner_core.plugin_runtime import PluginRunManager
from parallel_codex_runner_core.remote.controller import RemoteController, RemoteControllerError
from parallel_codex_runner_core.remote.ipc import PcrdServer
from parallel_codex_runner_core.remote.journal import CommandJournal
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from verify_synthesis_handoff_native import environment
from verify_synthesis_schema_native import Responses, verify


def completed(manager, run_id):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        run = manager.get_run(run_id, include_events=False)
        if run["status"] == "completed":
            assert all(a["status"] == "success" for a in run["agents"]), run
            return run
        if run["status"] in {"failed", "cancelled", "discarded"}:
            raise AssertionError({key: run.get(key) for key in ("status", "error", "agents")})
        time.sleep(0.05)
    raise AssertionError("Disposable native continuation did not complete")


def main():
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Responses)
    serving = threading.Thread(target=provider.serve_forever, daemon=True)
    serving.start()
    managers, servers, processes = [], [], []
    temporary = tempfile.TemporaryDirectory(prefix="pcr-original-recovery-")
    try:
        with mock.patch.dict(os.environ, PCR_INTERACTIVE_QA_AUTHORIZED="0"):
            root = Path(temporary.name).resolve()
            asyncio.run(verify(root, provider.server_address[1]))
            home = root / "source-home"
            with sqlite3.connect(home / "state_5.sqlite") as database:
                session = database.execute("SELECT id FROM threads WHERE id <> 'unrelated'").fetchone()[0]
            workspace = root / "work"
            workspace.mkdir()
            (workspace / "preserved.txt").write_text("keep original user result\n")
            state = root / "service"

            def open_service():
                manager = PluginRunManager(state / "plugin", detached_workers=True,
                    retain_history=True, run_maintenance=False, enforce_storage_quota=False)
                controller = RemoteController(state_dir=state, manager=manager)
                server = PcrdServer(root / "s", controller)
                managers.append(manager)
                servers.append((server, controller))
                return manager, server

            with environment(home, provider.server_address[1]):
                manager, server = open_service()
                source = manager.start_run("Native original source", str(workspace), num_agents=1,
                    model="schema-fixture-model", resume_session_id=session, sync_back=True,
                    codex_bin=str(Path.home() / "Library/Application Support/parallel-codex-runner/codex/0.159.2/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"))
                completed(manager, source["run_id"])
                request = RemoteRequest(action="turns.continue", client_id="mac-app",
                    command_id="preserved-original-attempt-1", body={"run_id": source["run_id"],
                        "agent": 1, "prompt": "Continue this exact saved original", "num_agents": 1,
                        "synthesis_agents": 0, "confirm_large_run": True})
                # Reproduce the old controller's misclassification after adoption,
                # not a new user input error or an unaccepted message assertion.
                legacy = RemoteControllerError("invalid_argument",
                    "Unsupported Codex history schema; no global database was copied: guardian_review_feedback, thread_read_receipts")
                deadline = time.monotonic() + 30
                with mock.patch.object(manager, "start_run", side_effect=legacy):
                    while True:
                        response = server.execute(request.to_mapping())
                        assert not response["ok"]
                        if response["error"]["code"] == "invalid_argument":
                            break
                        assert response["error"]["code"] == "pcr_operation_pending", response
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                source_after = manager.get_run(source["run_id"], include_events=False)
                assert source_after["status"] == "finalized" and source_after["finalized_agent"] == 1
                assert manager.can_retry_unstarted_continuation(source["run_id"], 1)
                original_record = server.journal._read(server.journal.directory / (server.journal._key(request) + ".json"))
                before_native = len(Responses.requests)
                server.server_close()
                processes.extend(manager._worker_processes.values())
                manager.close()
                servers.pop()[1].close()

                manager, server = open_service()
                assert server.prompt_receipt(request) == {"status": "retryable"}
                assert not server.journal.pending_requests(), "Must not automatically resurrect abandoned failures"
                with mock.patch.object(manager, "accept_agent", side_effect=AssertionError("Must not adopt twice")):
                    accepted = server.execute(request.to_mapping())
                assert accepted["ok"], accepted
                child_id = accepted["body"]["run_id"]
                # Discard the accepted reply, reconnect, and replay only the same
                # original command. Business identity must settle the lost receipt.
                server.server_close()
                processes.extend(manager._worker_processes.values())
                manager.close()
                servers.pop()[1].close()
                manager, server = open_service()
                repeated = server.execute(request.to_mapping())
                assert repeated["ok"] and repeated["body"]["run_id"] == child_id
                assert repeated["body"]["command_replayed"] is True
                final = completed(manager, child_id)
                assert final["config"]["submission_command"] == CommandJournal.identity(request)
                assert final["config"]["resume_session_id"] == source_after["session_promotion"]["session_id"]
                assert final["conversation_id"] == source_after["conversation_id"]
                assert final["continued_from_run_id"] == source["run_id"] and final["continued_from_agent"] == 1
                assert len(Responses.requests) - before_native == 1
                payload = json.loads((state / "plugin/runs.json").read_text())
                assert sum(r.get("continued_from_run_id") == source["run_id"] for r in payload["runs"]) == 1
                record = server.journal._read(server.journal.directory / (server.journal._key(request) + ".json"))
                assert record["history_recovery"]["error"] == original_record["error"]
                assert (workspace / "preserved.txt").read_text() == "keep original user result\n"
                print(json.dumps({"passed": True, "provider_scope": "loopback-only", "real_user_turns_submitted": 0,
                    "original_identity_preserved": True, "legacy_failure_retained": True,
                    "source_adoption_not_repeated": True, "native_continuation_submissions": 1,
                    "child_runs": 1, "native_result": "success", "lost_receipt_and_restart_recovered": True}))
    finally:
        for manager in managers:
            for run_id in list(manager._runs):
                FileSignal(manager.control_dir / run_id / "shutdown").set()
            processes.extend(manager._worker_processes.values())
        for server, controller in servers:
            server.server_close()
            controller.close()
        for manager in managers:
            manager.close()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
        provider.shutdown()
        provider.server_close()
        serving.join()
        temporary.cleanup()


if __name__ == "__main__":
    main()
