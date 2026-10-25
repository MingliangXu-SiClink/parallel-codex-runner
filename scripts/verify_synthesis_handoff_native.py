"""Replace a disposable 0.1.92 idle owner; complete only its three synthesis slots."""

import asyncio
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

from parallel_codex_runner_core.plugin.lifecycle import FileSignal, pid_is_alive
from test_synthesis_preparation_recovery import failed_synthesis
from verify_synthesis_schema_native import Responses, verify


@contextmanager
def environment(home, port):
    before = dict(os.environ)
    home.joinpath("config.toml").write_text(f'''model = "schema-fixture-model"
model_provider = "schema-fixture"
[model_providers.schema-fixture]
name = "schema-fixture"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = false
[features]
plugins = false
remote_plugin = false
apps = false
memories = false
''')
    os.environ.update(CODEX_HOME=str(home), PCR_INTERACTIVE_QA_AUTHORIZED="0", PCR_CODEX_RESUME_LOCK="",
                      NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        os.environ[key] = "http://127.0.0.1:9"
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Responses)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        with tempfile.TemporaryDirectory(prefix="pcr-synthesis-handoff-") as temporary:
            root = Path(temporary).resolve()
            asyncio.run(verify(root, server.server_address[1]))
            home = root / "source-home"
            with sqlite3.connect(home / "state_5.sqlite") as db:
                selected = db.execute("SELECT id FROM threads WHERE id <> 'unrelated'").fetchone()[0]
            case = root / "handoff"
            case.mkdir()
            fixture = failed_synthesis.__wrapped__(case)
            manager, run, _, checkpoint = next(fixture)
            original_candidates = {idx: value for idx, value in run.results.items() if idx <= 8}
            run.config.update(resume_session_id=selected, model="schema-fixture-model", synthesis_model="schema-fixture-model")
            manager._save_state_locked()
            old = None
            new_pid = None
            background = subprocess.Popen(["/bin/sleep", "120"])
            try:
                with environment(home, server.server_address[1]):
                    old_env = dict(os.environ)
                    old_env.pop("PYTHONPATH", None)
                    old = subprocess.Popen([str(Path.home() / "Library/Application Support/parallel-codex-runner/runtime/0.1.92/bin/python"),
                        "-m", "parallel_codex_runner_core.plugin.worker", "--state-dir", str(manager.state_dir), "--run-id", run.run_id],
                        env=old_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        live = json.loads(checkpoint.read_text())
                        if live.get("pid") == old.pid and live.get("alive"):
                            break
                        time.sleep(0.05)
                    else:
                        raise AssertionError("Disposable legacy worker did not become idle")
                    run = manager._require_run_locked(run.run_id)
                    assert run.worker_pid == old.pid
                    start_requests = len(Responses.requests)
                    response = manager.recover_synthesis_preparation(run.run_id, [9, 10, 11])
                    new_pid = response["worker_pid"]
                    assert new_pid != old.pid
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        actual = manager.get_run(run.run_id, include_events=False)
                        if actual["status"] == "completed":
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Native synthesis did not finish")
                    assert {row["status"] for row in actual["agents"] if row["role"] == "synthesis"} == {"success"}, [
                        {key: row.get(key) for key in ("agent", "role", "status", "error")} for row in actual["agents"]]
                    assert len(Responses.requests) - start_requests == 3
                    assert manager._runs[run.run_id].results.keys() == set(range(1, 12))
                    assert {idx: manager._runs[run.run_id].results[idx] for idx in range(1, 9)} == original_candidates
                    assert background.poll() is None
                    assert all(list((Path(run.run_root) / f"retry_history/agent_{idx:03d}").glob("*/status.json")) for idx in (9, 10, 11))
                    print(json.dumps({"legacy_idle_owner_replaced": True, "native_synthesis_success": 3,
                        "native_synthesis_submissions": 3, "candidate_results_unchanged": 8,
                        "background_process_unchanged": True, "original_failure_evidence_archived": True,
                        "provider_scope": "loopback-only", "real_user_turns_submitted": 0}))
            finally:
                FileSignal(manager.control_dir / run.run_id / "shutdown").set()
                if old is not None:
                    old.wait(timeout=10)
                deadline = time.monotonic() + 10
                while new_pid and pid_is_alive(new_pid) and time.monotonic() < deadline:
                    process = manager._worker_processes.get(run.run_id)
                    if process is not None and process.poll() is not None:
                        break
                    time.sleep(0.05)
                background.terminate()
                background.wait(timeout=10)
                try:
                    next(fixture)
                except StopIteration:
                    pass
    finally:
        server.shutdown()
        server.server_close()
        serving.join()


if __name__ == "__main__":
    main()
