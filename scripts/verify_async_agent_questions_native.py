"""Native Codex async questions and late answers, exclusively on a disposable loopback provider."""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import parallel_codex_runner_core
from parallel_codex_runner_core import app
from parallel_codex_runner_core.app import run_one_agent
from parallel_codex_runner_core.agent_questions import QuestionStore
from parallel_codex_runner_core.async_answers import run_idle_answer
from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.plugin_runtime import PluginRunManager


class Provider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []
    active = False
    def log_message(self, *_):
        pass

    def do_GET(self):
        data = b'{"data":[]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        encoded = json.dumps(body.get("input", []))
        if "fixture-async-call" not in encoded:
            item = {"type": "function_call", "id": "fixture-async-item", "call_id": "fixture-async-call",
                "name": "request_user_input_async", "status": "completed", "arguments": json.dumps({"questions": [{
                    "title": "Choose this original Agent's direction", "options": ["A", "B"]}]})}
            events = [
                {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": ""}},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": item["id"], "delta": item["arguments"]},
                {"type": "response.function_call_arguments.done", "output_index": 0, "item_id": item["id"], "arguments": item["arguments"]},
            ]
        else:
            if type(self).active and "send_user_message_question_reply" not in encoded:
                time.sleep(0.8)
            text = "ANSWER-B" if "send_user_message_question_reply" in encoded else "Independent work complete"
            item = {"type": "message", "id": "fixture-final", "role": "assistant", "phase": "final_answer",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}
            events = [
                {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
                {"type": "response.content_part.added", "output_index": 0, "item_id": item["id"], "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []}},
                {"type": "response.output_text.delta", "output_index": 0, "item_id": item["id"], "content_index": 0, "delta": text},
                {"type": "response.output_text.done", "output_index": 0, "item_id": item["id"], "content_index": 0, "text": text},
            ]
        response = {"id": "fixture-response", "object": "response", "status": "completed", "output": [item],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            *events, {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response}]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)


def verify(root, server, *, active=False):
    Provider.requests = []
    Provider.active = active
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "retained.txt").write_text("Existing successful work")
    meta = root / "run/meta/agent_007"
    meta.mkdir(parents=True)
    home = meta / "codex_home"
    home.mkdir()
    (root / "run/.pcr-plugin-run.json").write_text(json.dumps({"version": 2, "run_id": "native-async-fixture",
        "workspace": str(workspace), "artifact_token": "isolated-fixture", "supports_agent_questions": True}))
    (meta / "developer_instructions.json").write_text(json.dumps("This is an isolated loopback fixture. Work only in its temporary workspace."))
    provider = '{name="async-fixture",base_url="http://127.0.0.1:' + str(server.server_address[1]) + '/v1",wire_api="responses",requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}'
    command = [shutil.which("codex"), "exec", "-c", 'model_provider="async-fixture"', "-c", "model_providers.async-fixture=" + provider,
        "--disable", "plugins", "--disable", "remote_plugin", "--disable", "apps", "--disable", "memories",
        "--enable", "send_message_to_user_async",
        "-m", "gpt-6.1-sol", "--sandbox", "workspace-write", "--ask-for-approval", "never",
        "-o", str(meta / "final_message.md"), "-"]
    events = []
    store = QuestionStore(meta)
    failures = []
    def answer_live():
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                records = store.snapshot()
                if records:
                    record = records[0]
                    store.answer(record["id"], record["connection_id"], "live-answer",
                        {record["questions"][0]["id"]: {"answers": ["B"]}})
                    return
                time.sleep(0.01)
            failures.append("The native async question never appeared")
        except Exception as error:
            failures.append(str(error))
    answering = threading.Thread(target=answer_live, daemon=True) if active else None
    if answering:
        answering.start()
    first = asyncio.run(run_one_agent(7, workspace, meta, home, "Ask a direction and continue independent work.", command,
        progress_callback=events.append))
    if answering:
        answering.join(timeout=35)
        assert not answering.is_alive() and not failures, failures
    assert first.status == "success", asdict(first)
    if active:
        assert (meta / "final_message.md").read_text() == "ANSWER-B"
        assert store.snapshot()[0]["delivery"] == "sent", store.snapshot()
        native = [json.loads(event["text"]) for event in events if event.get("type") == "agent_line" and event.get("text", "").startswith("{")]
        assert sum(event.get("type") == "turn.started" for event in native) == 1
        print(json.dumps(dict(passed=True, mode="active-native-steering", original_agent=7,
            same_native_turn=True, original_task_replayed=False, provider_requests=len(Provider.requests))))
        return
    assert (meta / "final_message.md").read_text() == "Independent work complete"
    records = store.snapshot()
    assert len(records) == 1 and records[0]["kind"] == "async" and records[0]["state"] == "open", dict(records=records,
        stdout=(meta / "stdout.log").read_text(), provider_tools=[tool.get("name", tool.get("type")) for tool in Provider.requests[0].get("tools", [])])
    original = records[0]
    answers = {original["questions"][0]["id"]: {"answers": ["B"]}}
    store.answer(original["id"], original["connection_id"], "original-answer", answers)
    run = ManagedRun("native-async-fixture", "Original task", str(workspace), {}, run_root=str(root / "run"))
    result = run_idle_answer(run, 7, original["id"], asdict(first), events.append, threading.Event(), threading.Event())
    assert result.status == "success", result.error
    assert result.codex_thread_id == first.codex_thread_id == original["thread_id"]
    assert (meta / "final_message.md").read_text() == "ANSWER-B"
    assert (workspace / "retained.txt").read_text() == "Existing successful work"
    assert (meta / "answers" / original["id"] / "final_message.md").read_text() == "Independent work complete"
    assert store.get(original["id"])["delivery"] == "sent"
    final_input = Provider.requests[-1]["input"]
    assert "send_user_message_question_reply" in json.dumps(final_input)
    assert len(Provider.requests) == 3, len(Provider.requests)
    print(json.dumps(dict(passed=True, mode="completed-native-answer", native_version="0.159.2", original_agent=7,
        thread_preserved=True, original_task_replayed=False, original_result_preserved=True,
        native_reply_output="ANSWER-B", provider_requests=len(Provider.requests))))


def verify_managed(root, server, global_home):
    Provider.requests, Provider.active = [], False
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "retained.txt").write_text("Keep the original successful result")
    (global_home / "config.toml").write_text('model_provider = "async-fixture"\n'
        '[model_providers.async-fixture]\nname = "async-fixture"\nbase_url = "http://127.0.0.1:' + str(server.server_address[1]) +
        '/v1"\nwire_api = "responses"\nrequires_openai_auth = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
    manager = PluginRunManager(root / "state", detached_workers=True, retain_history=True, run_maintenance=False)
    try:
        started = manager.start_run("Ask a direction and continue independent work.", workspace=str(workspace),
            num_agents=1, synthesis_agents=0, model="gpt-6.1-sol", runs_dir=str(root / "runs"),
            supports_agent_questions=True, sync_back=False, keep_workspaces=True)
        run_id = started["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            run = manager.get_run(run_id)
            if run["status"] == "completed" and not run["worker_active"]:
                break
            time.sleep(0.05)
        assert run["status"] == "completed" and run["agents"][0]["status"] == "success", run
        original_owner = run["worker_pid"]
        detail = manager.get_agent(run_id, 1)
        record = next(record for record in detail["input_requests"] if record["kind"] == "async")
        answers = {record["questions"][0]["id"]: {"answers": ["B"]}}
        manager.answer_question(run_id, 1, record["id"], record["connection_id"], "managed-answer", answers)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            run = manager.get_run(run_id)
            detail = manager.get_agent(run_id, 1)
            if detail["final_message"] == "ANSWER-B" and run["status"] == "completed" and not run["worker_active"]:
                break
            time.sleep(0.05)
        assert detail["final_message"] == "ANSWER-B", dict(run=run, detail=detail)
        assert run["worker_pid"] != original_owner and run["finalized_agent"] is None and not run["synced_back"]
        receipt = manager.get_question(run_id, 1, record["id"])["request"]
        assert receipt["delivery"] == "sent" and receipt["thread_id"] == record["thread_id"]
        assert (workspace / "retained.txt").read_text() == "Keep the original successful result"
        assert len(Provider.requests) == 3, len(Provider.requests)
        print(json.dumps(dict(passed=True, mode="managed-idle-owner-handoff", same_run=True,
            same_agent=True, same_thread=True, adopted=False, original_task_replayed=False)))
    finally:
        # Only these disposable fixture owners are asked to exit, after they
        # are idle. No user process or real task is addressed by this script.
        from parallel_codex_runner_core.plugin.lifecycle import FileSignal, pid_is_alive
        for run in list(manager._runs.values()):
            FileSignal(manager.control_dir / run.run_id / "shutdown").set()
        deadline = time.monotonic() + 10
        while any(pid_is_alive(run.worker_pid) for run in manager._runs.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
        manager.close()


if __name__ == "__main__":
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[name] = "http://127.0.0.1:9"
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    os.environ["PCR_CODEX_RESUME_LOCK"] = ""
    with tempfile.TemporaryDirectory(prefix="pcr-async-native-") as directory:
        global_home = Path(directory) / "fixture-home"
        global_home.mkdir()
        os.environ["CODEX_HOME"] = str(global_home)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            original_environment = app.agent_process_environment
            def fixture_environment(*args):
                env = original_environment(*args)
                env["PYTHONPATH"] = str(Path(parallel_codex_runner_core.__file__).resolve().parents[1])
                return env
            with patch.object(app, "agent_process_environment", side_effect=fixture_environment):
                verify(Path(directory), server)
                active_root = Path(directory) / "active"
                active_root.mkdir()
                verify(active_root, server, active=True)
                if os.environ.get("PCR_VERIFY_MANAGED_ANSWERS") == "1":
                    verify_managed(Path(directory) / "managed", server, global_home)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
