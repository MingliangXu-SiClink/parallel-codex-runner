"""Real Codex + real pcrd IPC, using only a disposable loopback Responses fixture."""

import asyncio
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from parallel_codex_runner_core.codex_app_server import adapted_command
from parallel_codex_runner_core import codex_app_server as native_adapter
from parallel_codex_runner_core.agent_questions import QuestionStore
from parallel_codex_runner_core.plugin_runtime import ManagedRun, PluginRunManager
from parallel_codex_runner_core.remote.controller import RemoteController
from parallel_codex_runner_core.remote.ipc import PcrdClient, PcrdServer


class ResponsesFixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []
    errors = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "11")
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        outputs = [str(item.get("output", "")) for item in body.get("input", []) if item.get("type") == "function_call_output"]
        if not outputs:
            names = {tool.get("name") for tool in body.get("tools", [])}
            if "request_user_input" not in names:
                type(self).errors.append("Default mode did not expose request_user_input: " + str(names))
            call = {"type": "function_call", "id": "fixture-question", "call_id": "fixture-question-call",
                    "name": "request_user_input", "status": "completed", "arguments": json.dumps({"questions": [{
                        "id": "choice", "header": "Direction", "question": "Choose this Agent's direction",
                        "options": [{"label": "A", "description": "First direction"},
                                    {"label": "B", "description": "Second direction"}]}]})}
            item = call
            events = [
                {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": ""}},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": item["id"], "delta": item["arguments"]},
                {"type": "response.function_call_arguments.done", "output_index": 0, "item_id": item["id"], "arguments": item["arguments"]},
            ]
        else:
            try:
                answer = json.loads(outputs[-1])["answers"]["choice"]["answers"][0]
                assert answer in {"A", "B"}
            except (ValueError, KeyError, TypeError, AssertionError):
                type(self).errors.append("Native tool did not receive the original structured answer: " + repr(outputs))
                answer = "ERROR"
            text = "ANSWER-" + answer
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
        self.wfile.flush()
        self.close_connection = True


async def verify(root, responses):
    workspace = root / "workspace"
    workspace.mkdir()
    run_root = root / "runs/20261016_120000"
    run_root.mkdir(parents=True)
    manager = PluginRunManager(root / "state", detached_workers=False, run_maintenance=False)
    run = ManagedRun("question-native", "Loopback fixture only", str(workspace),
                     {"run_base": str(run_root.parent), "supports_agent_questions": True},
                     status="running", run_root=str(run_root), artifact_token="native-question-fixture",
                     agent_statuses={1: "running", 2: "running"})
    manager._runs[run.run_id] = run
    manager._artifacts.write_marker(run)
    controller = RemoteController(manager=manager, state_dir=root / "remote")
    # Keep the Unix path below macOS's sockaddr_un limit.
    socket_path = Path(tempfile.mkdtemp(prefix="pq-", dir="/tmp")) / "p.sock"
    server = PcrdServer(socket_path, controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    processes, readers, stderr_readers = [], [], []
    try:
        port = responses.server_address[1]
        provider = ('{name="question-fixture",base_url="http://127.0.0.1:' + str(port) +
                    '/v1",wire_api="responses",requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}')
        for agent in (1, 2):
            directory = run_root / f"meta/agent_{agent:03d}"
            directory.mkdir(parents=True)
            home = root / f"codex-home-{agent}"
            home.mkdir()
            command = [shutil.which("codex"), "exec", "-c", 'model_provider="question-fixture"',
                       "-c", "model_providers.question-fixture=" + provider,
                       "--disable", "plugins", "--disable", "remote_plugin", "--disable", "apps", "--disable", "memories",
                       "-m", "question-fixture-model", "--sandbox", "workspace-write", "--ask-for-approval", "never",
                       "-o", str(directory / "final.md"), "-"]
            env = {**os.environ, "CODEX_HOME": str(home), "PCR_CODEX_RESUME_LOCK": "",
                   "PYTHONPATH": str(Path(native_adapter.__file__).resolve().parents[1]),
                   "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
            for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
                env[key] = "http://127.0.0.1:9"
            process = await asyncio.create_subprocess_exec(*adapted_command(command), cwd=workspace, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            processes.append(process)
            process.stdin.write(b"Ask which direction to take. This is an isolated loopback test, not a user task.\n")
            await process.stdin.drain()
            process.stdin.close()
            readers.append(asyncio.create_task(process.stdout.read()))
            stderr_readers.append(asyncio.create_task(process.stderr.read()))

        async def ready(agent):
            store = QuestionStore(run_root / f"meta/agent_{agent:03d}")
            while True:
                pending = store.snapshot()
                if pending:
                    return pending[0]
                if processes[agent - 1].returncode is not None:
                    raise AssertionError("Native Agent exited without a question: " + repr(await stderr_readers[agent - 1]))
                await asyncio.sleep(0.025)

        records = await asyncio.wait_for(asyncio.gather(ready(1), ready(2)), 45)
        assert all(record["state"] == "open" for record in records), records
        original_threads = [record["thread_id"] for record in records]
        client = PcrdClient(socket_path)

        async def answer(agent, choice):
            record = records[agent - 1]
            body = {"run_id": run.run_id, "agent": agent, "request_id": record["id"],
                    "connection_id": record["connection_id"], "answer_id": f"fixture-answer-{agent}",
                    "answers": {"choice": {"answers": [choice]}}}
            for _ in range(2):
                result = await asyncio.to_thread(client.request, "runs.answer_question", body,
                                                 command_id=f"fixture-answer-{agent}")
                assert result["request"]["answers"] == body["answers"]

        await answer(2, "B")
        await asyncio.wait_for(processes[1].wait(), 30)
        assert processes[1].returncode == 0
        assert processes[0].returncode is None, "Answering B affected Agent A"
        # Restart only this disposable IPC server; both native owners live outside it.
        receipt_store_id = server.journal.store_id
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()
        controller = RemoteController(manager=manager, state_dir=root / "remote")
        server = PcrdServer(socket_path, controller)
        assert server.journal.store_id == receipt_store_id
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = PcrdClient(socket_path)
        check = await asyncio.to_thread(client.request, "runs.question", {
            "run_id": run.run_id, "agent": 1, "request_id": records[0]["id"]})
        assert check["request"]["thread_id"] == original_threads[0]
        assert check["request"]["connection_id"] == records[0]["connection_id"]
        await answer(1, "A")
        await asyncio.wait_for(processes[0].wait(), 30)
        assert processes[0].returncode == 0
        assert [(run_root / f"meta/agent_{i:03d}/final.md").read_text() for i in (1, 2)] == ["ANSWER-A", "ANSWER-B"]
        assert not ResponsesFixture.errors, ResponsesFixture.errors
        logs = [json.loads(line) for reader in readers for line in (await reader).decode().splitlines()]
        assert sum(event.get("type") == "turn.started" for event in logs) == 2
        assert sum(event.get("type") == "thread.started" for event in logs) == 2
        assert sum(event.get("type") == "pcr.input_request" for event in logs) == 2
        assert len(ResponsesFixture.requests) == 4, "A task or answer was repeated"
        assert run.finalized_agent is None and run.agent_statuses == {1: "running", 2: "running"}
        print(json.dumps({"scope": "loopback-only", "real_codex_agents": 2, "real_pcrd_ipc": True,
                          "default_execution_mode": True, "original_turns": 2,
                          "native_is_blocking": [record["is_blocking"] for record in records],
                          "different_answers": ["A", "B"], "duplicate_submissions_safe": True,
                          "client_reconnection_safe": True, "ipc_server_restart_safe": True,
                          "other_agent_unaffected": True,
                          "provider_requests": len(ResponsesFixture.requests), "real_user_turns": 0}))
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
        await asyncio.gather(*(p.wait() for p in processes))
        await asyncio.gather(*readers, *stderr_readers, return_exceptions=True)
        server.shutdown()
        server.server_close()
        thread.join()
        shutil.rmtree(socket_path.parent)


def main():
    responses = ThreadingHTTPServer(("127.0.0.1", 0), ResponsesFixture)
    thread = threading.Thread(target=responses.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="pcr-native-questions-") as tmp:
            asyncio.run(verify(Path(tmp).resolve(), responses))
    finally:
        responses.shutdown()
        responses.server_close()
        thread.join()


if __name__ == "__main__":
    main()
