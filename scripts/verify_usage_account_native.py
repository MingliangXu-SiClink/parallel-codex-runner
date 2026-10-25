"""A real native quota reader must never own or refresh the primary login."""
import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from parallel_codex_runner_core.codex_usage import CodexUsageReader, UsageReadError, read_codex_usage
from verify_account_follow_native import Fixture, ThreadingHTTPServer, fixture_auth


class UsageFixture(Fixture):
    def do_GET(self):
        if "/usage" not in self.path:
            super().do_GET()
            return
        self.server.usage_accounts.append(self.headers.get("chatgpt-account-id"))
        if self.server.change_account:
            (self.server.source / "auth.json").write_text(json.dumps(self.server.auth_c))
        if self.server.reject:
            self.reply(b'{"error":{"message":"synthetic token expired"}}', status=401)
        else:
            self.reply(json.dumps({"plan_type": "pro", "rate_limit": {
                "allowed": True, "limit_reached": False, "primary_window": {
                    "used_percent": 13, "limit_window_seconds": 604800,
                    "reset_after_seconds": 1000, "reset_at": int(time.time()) + 1000}}}).encode())

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/oauth/token":
            self.server.oauth_calls += 1
        if self.path.endswith("/responses"):
            self.server.model_calls += 1
        self.reply(b"{}")


def verify(root, *, reject=False, change=False):
    source = root / "selected"
    source.mkdir()
    before = fixture_auth("fixture-b")
    (source / "auth.json").write_text(json.dumps(before))
    server = ThreadingHTTPServer(("127.0.0.1", 0), UsageFixture)
    server.source, server.auth_c = source, fixture_auth("fixture-c")
    server.bound_b, server.bound_c = threading.Event(), threading.Event()
    server.usage_accounts, server.oauth_calls, server.model_calls = [], 0, 0
    server.reject, server.change_account = reject, change
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    (source / "config.toml").write_text(f'chatgpt_base_url="http://127.0.0.1:{port}/backend-api/"\n')
    env = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
               CODEX_REFRESH_TOKEN_URL_OVERRIDE=f"http://127.0.0.1:{port}/oauth/token")
    for key in list(env):
        if key.startswith(("OPENAI_", "CODEX_AUTH", "CODEX_API", "CODEX_ACCESS")):
            env.pop(key)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = "http://127.0.0.1:9"
    try:
        with patch.dict(os.environ, env, clear=True):
            if change:
                value = CodexUsageReader().read("/opt/homebrew/bin/codex", source)
                assert value["status"] == "unavailable" and not value["limits"]
            elif reject:
                try:
                    asyncio.run(read_codex_usage("/opt/homebrew/bin/codex", source))
                except UsageReadError as error:
                    assert str(error) == "unavailable"
                else:
                    raise AssertionError("A rejected external token was not reported unavailable")
            else:
                value = asyncio.run(read_codex_usage("/opt/homebrew/bin/codex", source))
                assert value["status"] == "available" and value["limits"][0]["windows"][0]["used_percent"] == 13
        expected = server.auth_c if change else before
        assert json.loads((source / "auth.json").read_text()) == expected
        assert set(server.usage_accounts) == {"fixture-b"}
        assert server.oauth_calls == server.model_calls == 0
        print(json.dumps(dict(case="source-change" if change else "expired" if reject else "quota",
            primary_login_preserved=True, native_oauth_refreshes=0, model_requests=0,
            old_result_cannot_commit_to_new_account=change, production_requests=0)))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pcr-usage-native-") as temporary:
        for name, reject, change in (("quota", False, False), ("expired", True, False), ("change", False, True)):
            root = Path(temporary) / name
            root.mkdir()
            verify(root, reject=reject, change=change)
