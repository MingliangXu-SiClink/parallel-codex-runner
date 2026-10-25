"""Offline native recovery acceptance using a loopback-only Responses fixture."""

import asyncio
import contextlib
import io
import json
import os
import re
import shlex
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from parallel_codex_runner_core import codex_app_server as adapter


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []
    job_file = None
    background = False
    routing_timeout = False

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        payload = b'{"data":[]}'
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        native_input = json.dumps(body)
        recovering = adapter.RECOVERY_INPUT in native_input
        has_tool_output = any(item.get("type") == "function_call_output" for item in body.get("input", []))
        outputs = "\n".join(str(item.get("output") or "") for item in body.get("input", [])
                            if item.get("type") == "function_call_output")
        if not recovering and has_tool_output:
            payload = b'{"error":{"message":"server overloaded","type":"server_error"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        message = "Recovered final answer" if recovering else "Saved partial progress"
        item = {"type": "message", "id": "message", "role": "assistant",
                "phase": "final_answer" if recovering else "commentary",
                "content": [{"type": "output_text", "text": message, "annotations": []}]}
        response = {"id": "response", "object": "response", "status": "completed",
                    "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0,
             "item": {**item, "content": []}},
            {"type": "response.content_part.added", "output_index": 0, "item_id": "message",
             "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
            {"type": "response.output_text.delta", "output_index": 0, "item_id": "message",
             "content_index": 0, "delta": message},
            {"type": "response.output_text.done", "output_index": 0, "item_id": "message",
             "content_index": 0, "text": message},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
        ]
        call = None
        if recovering:
            if not has_tool_output:
                raise AssertionError("Native recovery lost the completed tool output")
            if self.background and "background survived" not in outputs:
                session = re.search(r"Process running with session ID (\d+)", outputs)
                if session is None:
                    raise AssertionError("Native background terminal was not retained")
                call = {"name": "write_stdin", "arguments": json.dumps({
                    "session_id": int(session[1]), "chars": "", "yield_time_ms": 1000})}
            else:
                events.append({"type": "response.completed", "response": response})
        else:
            command = "printf 'synthetic job\\n' >> " + shlex.quote(str(self.job_file))
            if self.background:
                command += "; sleep 2; printf 'background survived\\n'"
            call = {"name": "exec_command", "arguments": json.dumps({
                "cmd": command, "yield_time_ms": 1 if self.background else 1000})}
        if call is not None:
            call = {"type": "function_call", "id": "job-item", "call_id": "fixture-" + str(len(Fixture.requests)),
                    "status": "completed", **call}
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": {**call, "arguments": ""}},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "job-item", "delta": call["arguments"]},
                {"type": "response.function_call_arguments.done", "output_index": 0, "item_id": "job-item", "arguments": call["arguments"]},
                {"type": "response.output_item.done", "output_index": 0, "item": call},
                {"type": "response.completed", "response": {**response, "output": [call]}},
            ]
        payload = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()
        self.close_connection = True


async def verify(root, port, *, lost_receipt=False, process_exit=False):
    original = root / "existing-result.bin"
    original.write_bytes(b"existing experiment output")
    Fixture.job_file = root / "job-starts.log"
    output = root / "meta" / "final.md"
    output.parent.mkdir()
    provider = ('{name="recovery-fixture",base_url="http://127.0.0.1:' + str(port) +
                '/v1",wire_api="responses",requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}')
    command = ["/opt/homebrew/bin/codex", "exec", "-c", 'model_provider="recovery-fixture"',
               "-c", "model_providers.recovery-fixture=" + provider,
               "--disable", "plugins", "--disable", "remote_plugin", "--disable", "apps", "--disable", "memories",
               "-m", "recovery-fixture-model", "--sandbox", "workspace-write", "--ask-for-approval", "never",
               "-o", str(output), "-"]
    events = io.StringIO()
    real_wait = adapter.AgentRecovery.wait
    real_observe = adapter.EventAdapter.observe
    recovery_triggers = []
    interrupted = False

    def observed_terminal(instance, method, params):
        nonlocal interrupted
        if (process_exit and not interrupted and method == "item/completed"
            and params.get("item", {}).get("type") == "commandExecution"):
            interrupted = True
            asyncio.current_task().cancel()
        if Fixture.routing_timeout and method == "turn/completed" and params.get("turn", {}).get("status") == "failed":
            # Reproduce the captured final metadata downgrade, not a live provider outage.
            params = {**params, "turn": {**params["turn"], "error": {
                "message": "workspace routing discovery timed out", "codexErrorInfo": "other"}}}
        return real_observe(instance, method, params)

    async def receipt_fault(state, error, emit):
        recovery_triggers.append(error)
        if lost_receipt and not state.state.get("recoveries"):
            # Simulate loss of the turn receipt after the native side effect.
            state.update(stage="submitting", turn_id="")
        await real_wait(state, error, emit)

    with patch.object(adapter, "RECOVERY_MIN_DELAY", 0.01), patch.object(adapter, "RECOVERY_MAX_DELAY", 0.02), \
         patch.object(adapter.AgentRecovery, "wait", receipt_fault), \
         patch.object(adapter.EventAdapter, "observe", observed_terminal), \
         patch.object(adapter.asyncio, "create_subprocess_exec", wraps=adapter.asyncio.create_subprocess_exec) as spawn, \
         contextlib.redirect_stdout(events):
        async def execute():
            return await adapter.run(command, "Offline fixture task; preserve the existing output.",
                                     automatic_recovery=True)

        if process_exit:
            try:
                await asyncio.wait_for(execute(), timeout=60)
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Native process interruption was not exercised")
            saved = json.loads((output.parent / "execution_recovery.json").read_text())
            if saved["stage"] != "accepted" or not saved["turn_id"]:
                raise AssertionError("Accepted original execution was not retained")
        result = await asyncio.wait_for(execute(), timeout=60)
    if result != 0 or output.read_text() != "Recovered final answer":
        raise AssertionError("Native recovery did not finish: " + events.getvalue()[-6000:])
    if original.read_bytes() != b"existing experiment output":
        raise AssertionError("Existing output was modified")
    if Fixture.job_file.read_text().splitlines() != ["synthetic job"]:
        raise AssertionError("The synthetic job was not executed exactly once")
    if Fixture.background and not any("background survived" in str(item.get("output") or "")
            for item in Fixture.requests[-1]["input"] if item.get("type") == "function_call_output"):
        raise AssertionError("The original background terminal did not produce its retained final output")
    state = json.loads((output.parent / "execution_recovery.json").read_text())
    if (not process_exit and not state["recoveries"]) or state["stage"] != "completed":
        raise AssertionError("Recovery receipt was not durable")
    if spawn.await_count != (2 if process_exit else 1):
        raise AssertionError("The healthy native server or its terminals were restarted")
    if process_exit and (state["thread_id"] != saved["thread_id"] or state["turn_id"] == saved["turn_id"]):
        raise AssertionError("Process recovery did not continue the original private thread")
    native_errors = [event["error"] for line in events.getvalue().splitlines()
                     if (event := json.loads(line)).get("type") == "pcr.recovery" and event.get("error")]
    if Fixture.routing_timeout and not any(error.get("codexErrorInfo") == "other"
            and error.get("message") == "workspace routing discovery timed out" for error in recovery_triggers):
        raise AssertionError("The captured terminal other error did not itself trigger recovery")
    print(json.dumps({"native_exit": result, "state": state["stage"], "recoveries": state["recoveries"],
                      "provider_requests": len(Fixture.requests), "original_output_preserved": True,
                      "provider_scope": "loopback-only", "user_turns_submitted": 0,
                      "completed_tool_output_preserved": True, "synthetic_job_executions": 1,
                      "lost_receipt_reconciled": lost_receipt, "native_servers_started": spawn.await_count,
                      "background_terminal_survived": Fixture.background,
                      "accepted_execution_process_exit_recovered": process_exit,
                      "routing_timeout_recovered": Fixture.routing_timeout,
                      "terminal_error_injected": Fixture.routing_timeout,
                      "recovery_trigger_errors": recovery_triggers,
                      "native_error_info": [error.get("codexErrorInfo") for error in native_errors]}))


def main():
    with tempfile.TemporaryDirectory(prefix="pcr-native-recovery-") as tmp:
        root = Path(tmp).resolve()
        home = root / "isolated-codex-home"
        home.mkdir()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cwd = Path.cwd()
        try:
            for name, lost_receipt, background, routing, process_exit in (("stream-failure", False, False, False, False),
                    ("lost-receipt", True, False, False, False), ("background-terminal", False, True, False, False),
                    ("routing-timeout", False, False, True, False), ("process-exit", False, False, False, True)):
                case = root / name
                case.mkdir()
                Fixture.requests = []
                Fixture.background = background
                Fixture.routing_timeout = routing
                os.chdir(case)
                with patch.dict(os.environ, {"CODEX_HOME": str(home), "PCR_CODEX_RESUME_LOCK": "",
                                            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                                            "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                                            "ALL_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                                            "http_proxy": "http://127.0.0.1:9", "all_proxy": "http://127.0.0.1:9"}):
                    asyncio.run(verify(case, server.server_address[1], lost_receipt=lost_receipt, process_exit=process_exit))
        finally:
            os.chdir(cwd)
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
