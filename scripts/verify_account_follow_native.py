"""Offline, real-Codex acceptance for account changes during an existing task."""

import asyncio
import base64
import contextlib
import io
import json
import os
import shlex
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from parallel_codex_runner_core import codex_app_server as adapter
from parallel_codex_runner_core.codex_auth import SOURCE_ENV, file_auth


def fixture_auth(account, revision=1, user=None):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    user = user or "user-" + account
    claims = {"sub": user, "email": account + "@example.invalid", "revision": revision,
              "exp": int(time.time()) + 3600, "https://api.openai.com/auth": {
                  "chatgpt_account_id": account, "chatgpt_user_id": user, "chatgpt_plan_type": "pro"}}
    token = part({"alg": "none"}) + "." + part(claims) + ".synthetic"
    return {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {
        "id_token": token, "access_token": token, "refresh_token": "synthetic-" + account,
        "account_id": account}, "last_refresh": "2026-10-21T00:00:00Z"}


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, payload, *, status=200, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(payload)
            self.wfile.flush()
        self.close_connection = True

    def do_GET(self):
        if "accounts/check" in self.path:
            account = self.headers.get("chatgpt-account-id")
            if account == "fixture-b":
                self.server.bound_b.set()
            if account == "fixture-c":
                self.server.bound_c.set()
            value = {"accounts": [{"id": name, "workspace_backend_origin": "https://chatgpt.com",
                "account_routing_override": "NO_CONSTRAINT"} for name in ("fixture-a", "fixture-b", "fixture-c")]}
        elif "models" in self.path:
            value = {"models": []}
        else:
            value = {}
        self.reply(json.dumps(value).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path == "/oauth/token":
            self.server.refreshes += 1
            if self.server.late_refresh:
                (self.server.source / "auth.json").write_text(json.dumps(self.server.auth_c))
            self.reply(json.dumps(self.server.auth_b2["tokens"]).encode())
            return
        if not self.path.endswith("/responses"):
            self.reply(b"{}")
            return
        bearer = self.headers.get("Authorization")
        account = self.headers.get("chatgpt-account-id")
        revision = next((name for name, auth in self.server.auths.items()
                         if bearer == "Bearer " + auth["tokens"]["access_token"]), "unknown")
        self.server.requests.append({"account": account, "revision": revision, "input": body.get("input", [])})
        outputs = "\n".join(str(item.get("output") or "") for item in body.get("input", [])
                            if item.get("type") == "function_call_output")
        if not outputs:
            if adapter.RECOVERY_INPUT in json.dumps(body):
                command = "cat " + shlex.quote(str(self.server.job_result))
            else:
                command = ("printf 'one job\\n' >> " + shlex.quote(str(self.server.job)) +
                    "; sleep 4; printf 'background retained\\n' > " + shlex.quote(str(self.server.job_result)))
            call = {"name": "exec_command", "arguments": json.dumps({"cmd":
                command, "yield_time_ms": 1})}
        elif "background retained" not in outputs:
            if not self.server.changed:
                self.server.changed = True
                (self.server.source / "auth.json").write_text(json.dumps(self.server.auth_b))
                if not self.server.bound_b.wait(15):
                    self.server.failure = "Native account B was not acknowledged"
                    self.reply(b'{"error":{"message":"fixture handoff failed"}}', status=500)
                    return
            call = {"name": "exec_command", "arguments": json.dumps({
                "cmd": "cat " + shlex.quote(str(self.server.job_result)), "yield_time_ms": 1000})}
        else:
            if self.server.refresh_case and revision == "b":
                self.server.rejected = True
                self.reply(b'{"error":{"message":"synthetic token expired"}}', status=401)
                return
            if self.server.late_refresh and revision == "b2":
                if not self.server.bound_c.wait(15):
                    self.server.failure = "Native account C was not restored after late B refresh"
            call = None
        item = ({"type": "function_call", "id": "tool-" + str(len(self.server.requests)),
                 "call_id": "call-" + str(len(self.server.requests)), "status": "completed", **call}
                if call else {"type": "message", "id": "final", "role": "assistant", "phase": "final_answer",
                              "content": [{"type": "output_text", "text": "Account follow complete", "annotations": []}]})
        response = {"id": "fixture-response", "object": "response", "status": "completed", "output": [item],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                  {"type": "response.output_item.added", "output_index": 0, "item": item},
                  {"type": "response.output_item.done", "output_index": 0, "item": item},
                  {"type": "response.completed", "response": response}]
        self.reply("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode(), kind="text/event-stream")


async def verify(root, *, refresh=False, late_refresh=False):
    source, private = root / "selected", root / "private"
    source.mkdir()
    private.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.source, server.job, server.job_result = source, root / "job.txt", root / "job-result.txt"
    server.auth_a, server.auth_b, server.auth_b2 = fixture_auth("fixture-a"), fixture_auth("fixture-b"), fixture_auth("fixture-b", 2)
    server.auth_c = fixture_auth("fixture-c")
    server.auths = {"a": server.auth_a, "b": server.auth_b, "b2": server.auth_b2, "c": server.auth_c}
    server.requests, server.refreshes, server.changed, server.rejected = [], 0, False, False
    server.bound_b, server.failure, server.refresh_case = threading.Event(), None, refresh
    server.bound_c = threading.Event()
    server.late_refresh = late_refresh
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    config = f'chatgpt_base_url="http://127.0.0.1:{port}/backend-api/"\ncli_auth_credentials_store="file"\n'
    for home in (source, private):
        (home / "auth.json").write_text(json.dumps(server.auth_a))
        (home / "config.toml").write_text(config)
    existing = root / "existing-result.bin"
    existing.write_bytes(b"original result")
    output = root / "meta" / "final.md"
    output.parent.mkdir()
    provider = f'{{name="account-fixture",base_url="http://127.0.0.1:{port}/v1",wire_api="responses",requires_openai_auth=true,request_max_retries=0,stream_max_retries=0}}'
    command = ["/opt/homebrew/bin/codex", "exec", "-c", 'model_provider="account-fixture"',
               "-c", "model_providers.account-fixture=" + provider,
               "--disable", "plugins", "--disable", "remote_plugin", "--disable", "apps", "--disable", "memories",
               "-m", "account-fixture-model", "--sandbox", "workspace-write", "--ask-for-approval", "never", "-o", str(output), "-"]
    events, cwd = io.StringIO(), Path.cwd()
    env = dict(os.environ, CODEX_HOME=str(private), PCR_CODEX_RESUME_LOCK="", NO_PROXY="127.0.0.1,localhost",
               no_proxy="127.0.0.1,localhost", CODEX_REFRESH_TOKEN_URL_OVERRIDE=f"http://127.0.0.1:{port}/oauth/token")
    env[SOURCE_ENV] = str(source)
    for key in list(env):
        if key.startswith(("OPENAI_", "CODEX_AUTH", "CODEX_API", "CODEX_ACCESS")):
            env.pop(key)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = "http://127.0.0.1:9"
    try:
        os.chdir(root)
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(events), \
             patch.object(adapter.asyncio, "create_subprocess_exec", wraps=adapter.asyncio.create_subprocess_exec) as spawn:
            code = await asyncio.wait_for(adapter.run(command, "Synthetic task; keep the original result.", automatic_recovery=True), 45)
        if code or output.read_text() != "Account follow complete":
            raise AssertionError("selected fixture account=" + str(file_auth(source).account if file_auth(source) else None)
                                 + "\n" + events.getvalue()[-3000:])
        state = json.loads((output.parent / "execution_recovery.json").read_text())
        parsed = [json.loads(line) for line in events.getvalue().splitlines()]
        assert server.failure is None, server.failure
        assert server.job.read_text().splitlines() == ["one job"]
        assert existing.read_bytes() == b"original result"
        turns = 3 if late_refresh else 2
        assert len([event for event in parsed if event.get("type") == "turn.started"]) == turns
        threads = [event["thread_id"] for event in parsed if event.get("type") == "thread.started"]
        assert len(threads) == turns and len(set(threads)) == 1
        assert state["auth"]["identity"] == file_auth(source).identity
        assert state["auth"]["status"] == "current"
        assert all(request["revision"] in {"b", "b2", "c"} for request in server.requests[2:])
        assert server.requests[-1]["revision"] == ("c" if late_refresh else "b2" if refresh else "b")
        if late_refresh:
            assert json.loads((source / "auth.json").read_text())["tokens"] == server.auth_c["tokens"]
        else:
            assert json.loads((source / "auth.json").read_text())["tokens"] == server.auth_b["tokens"]
        assert server.refreshes == (1 if refresh else 0)
        assert spawn.await_count == turns
        print(json.dumps({"case": "late-refresh" if late_refresh else "refresh" if refresh else "switch", "native_exit": code,
            "native_thread_retained": True, "turns_started": turns, "original_prompt_replayed": False,
            "background_job_finished_before_native_restart": True, "job_starts": 1, "original_result_retained": True,
            "inference_accounts": [request["revision"] for request in server.requests], "native_refreshes": server.refreshes,
            "late_refresh_cannot_overwrite_new_account": late_refresh, "production_requests": 0}))
    finally:
        os.chdir(cwd)
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pcr-account-follow-") as tmp:
        for name, refresh, late in (("switch", False, False), ("refresh", True, False), ("late-refresh", True, True)):
            root = Path(tmp).resolve() / name
            root.mkdir()
            asyncio.run(verify(root, refresh=refresh, late_refresh=late))
