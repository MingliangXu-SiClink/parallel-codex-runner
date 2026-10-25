"""Exercise v61 history isolation with real Codex and loopback-only model traffic."""

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from parallel_codex_runner_core import app
from parallel_codex_runner_core.codex_app_server import adapted_command
from parallel_codex_runner_core.scoped_history import _schema


class Responses(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        text = "SCHEMA-HISTORY-SENTINEL"
        item = {"type": "message", "id": "fixture-answer", "role": "assistant", "phase": "final_answer",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}
        response = {"id": "fixture-response", "object": "response", "status": "completed", "output": [item],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
            {"type": "response.content_part.added", "output_index": 0, "item_id": item["id"], "content_index": 0,
             "part": {"type": "output_text", "text": "", "annotations": []}},
            {"type": "response.output_text.delta", "output_index": 0, "item_id": item["id"], "content_index": 0, "delta": text},
            {"type": "response.output_text.done", "output_index": 0, "item_id": item["id"], "content_index": 0, "text": text},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True


async def execute(root, home, port, resume=None):
    root.mkdir(parents=True)
    meta = root / "meta"
    meta.mkdir()
    output = meta / "final.md"
    provider = '{name="schema-fixture",base_url="http://127.0.0.1:' + str(port) + '/v1",wire_api="responses",requires_openai_auth=false}'
    command = [shutil.which("codex"), "exec", *(["resume"] if resume else []),
        "-c", 'model_provider="schema-fixture"', "-c", "model_providers.schema-fixture=" + provider,
        "--disable", "plugins", "--disable", "remote_plugin", "--disable", "apps", "--disable", "memories",
        "-m", "schema-fixture-model", "--sandbox", "workspace-write", "--ask-for-approval", "never",
        "-o", str(output), *([resume] if resume else []), "-"]
    env = {**os.environ, "CODEX_HOME": str(home), "PCR_CODEX_RESUME_LOCK": "",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env[key] = "http://127.0.0.1:9"
    process = await asyncio.create_subprocess_exec(*adapted_command(command), cwd=root, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(b"Synthetic loopback history check only.\n"), 60)
    except BaseException:
        if process.returncode is None:
            process.terminate()
        await process.wait()
        raise
    if process.returncode or not output.exists() or output.read_text() != "SCHEMA-HISTORY-SENTINEL":
        raise AssertionError((process.returncode, stdout.decode()[-5000:], stderr.decode()[-2500:]))
    receipt = json.loads((meta / "execution_recovery.json").read_text())
    assert receipt["stage"] == "completed" and receipt["thread_id"]
    if resume:
        assert receipt["thread_id"] == resume
    return receipt["thread_id"]


async def verify(root, port):
    home = root / "source-home"
    home.mkdir()
    selected = await execute(root / "seed", home, port)
    database = home / "state_5.sqlite"
    contract = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/codex-state-v61.json").read_text())
    with closing(sqlite3.connect(database)) as db:
        latest = db.execute("SELECT max(version) FROM _sqlx_migrations").fetchone()[0]
        assert latest in (58, 59, 60, 61), ("native_schema_version", latest)
        for version, table in ((59, "idx_thread_attachments_identity_thread"), (60, "guardian_review_feedback"), (61, "thread_read_receipts")):
            if latest < version:
                for kind, name, owner, sql in contract["schema"]:
                    if (owner == table or name == table) and sql:
                        db.execute(sql)
                _, description, success, checksum = next(m for m in contract["migrations"] if m[0] == version)
                db.execute("INSERT INTO _sqlx_migrations VALUES (?,?,'2026-10-16',?,?,0)",
                    (version, description, success, bytes.fromhex(checksum)))
        db.execute("""INSERT INTO threads (id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode)
            VALUES ('unrelated','/nonexistent/unrelated',1,1,'cli','test','/unrelated','unrelated','{}','never')""")
        for thread in (selected, "unrelated"):
            db.execute("INSERT OR REPLACE INTO guardian_review_feedback VALUES (?,?,?)", (thread, thread, f"{thread}-private-review".encode()))
            db.execute("INSERT OR REPLACE INTO thread_read_receipts VALUES (?,NULL,'fixture-revision',NULL)", (thread,))
        db.commit()
        _schema(db, "state")
    projection = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/codex-projection-v8.json").read_text())
    with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as db:
        latest = db.execute("SELECT max(version) FROM _sqlx_migrations").fetchone()[0]
        assert latest in (7, 8), ("native_projection_version", latest)
        if latest == 7:
            db.execute("ALTER TABLE thread_turns ADD COLUMN root_turn_id TEXT")
            version, description, success, checksum = projection["migrations"][-1]
            db.execute("INSERT INTO _sqlx_migrations VALUES (?,?,'2026-10-16',?,?,0)",
                (version, description, success, bytes.fromhex(checksum)))
            db.commit()
        _schema(db, "projection")
    original = hashlib.sha256(database.read_bytes()).hexdigest()
    source = app.AgentCodexSource(home, selected, root / "prepared-source")
    try:
        for idx in range(3):
            private = root / f"agent-{idx}-home"
            workspace = root / f"agent-{idx}"
            app.prepare_agent_codex_home(home, private, workspace, selected, source)
            with closing(sqlite3.connect(private / "state_5.sqlite")) as db:
                assert db.execute("SELECT id FROM threads").fetchall() == [(selected,)]
                assert db.execute("SELECT thread_id FROM guardian_review_feedback").fetchall() == [(selected,)]
                assert db.execute("SELECT thread_id FROM thread_read_receipts").fetchall() == [(selected,)]
            await execute(workspace, private, port, selected)
    finally:
        source.close()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original
    assert len(Responses.requests) == 4
    for request in Responses.requests[1:]:
        assert "SCHEMA-HISTORY-SENTINEL" in json.dumps(request["input"])
        assert "unrelated-private-review" not in json.dumps(request)
    print(json.dumps({"real_native_resume_count": 3, "native_requests": 4, "same_original_thread": True,
        "history_retained": True, "unrelated_rows_excluded": True, "original_database_unchanged": True,
        "provider_scope": "loopback-only", "real_user_turns_submitted": 0}))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Responses)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="pcr-schema-native-") as temporary:
            asyncio.run(verify(Path(temporary).resolve(), server.server_address[1]))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
