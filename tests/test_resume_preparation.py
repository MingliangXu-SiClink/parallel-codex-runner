import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core import app


class SerializedPathTests(unittest.TestCase):
    def test_discovers_default_custom_and_nested_paths(self):
        roots = (
            "/Users/example/.codex_parallel_runs/20261002_104147",
            "/tmp/custom runs/20261002_104147",
        )
        for root in roots:
            with self.subTest(root=root):
                workspace = root + "/workspaces/agent_007"
                home = root + "/meta/agent_007/codex_home"
                value = {workspace + "/notes.md": [{"cwd": workspace, "home": home + "/skills/test"}]}
                self.assertEqual(app._serialized_pcr_workspace_paths(value), {workspace})
                self.assertEqual(app._serialized_pcr_codex_home_paths(value), {home})

    def test_multiple_paths_remain_separate(self):
        first = "/tmp/first/20261002_104147/workspaces/agent_001"
        second = "/tmp/second/20261002_104148/workspaces/agent_002"
        value = f'command "{first}/a"\n<{second}/b>\r\n'
        self.assertEqual(app._serialized_pcr_workspace_paths(value), {first, second})

    def test_path_prefix_does_not_cross_delimiters_or_empty_components(self):
        root = "/tmp/research folder/20261002_104147/workspaces/agent_003"
        for prefix in ('"', "'", "\r", "\n", "<", ">", "/"):
            with self.subTest(prefix=prefix):
                self.assertEqual(app._serialized_pcr_workspace_paths("/unrelated" + prefix + root), {root})

    def test_non_path_payload_never_enters_regex(self):
        value = "data:image/png;base64," + "abc/def/ghi/" * 100_000
        with mock.patch.object(app, "_PCR_AGENT_WORKSPACE_RE") as workspace_pattern:
            self.assertEqual(app._serialized_pcr_workspace_paths(value), set())
            workspace_pattern.finditer.assert_not_called()
        with mock.patch.object(app, "_PCR_AGENT_CODEX_HOME_RE") as home_pattern:
            self.assertEqual(app._serialized_pcr_codex_home_paths(value), set())
            home_pattern.finditer.assert_not_called()

    def test_slash_rich_output_with_false_marker_has_bounded_cost(self):
        value = "abc/def/ghi/" * 100_000 + "/workspaces/agent_fake /meta/agent_fake/codex_home"
        started = time.monotonic()
        self.assertEqual(app._serialized_pcr_workspace_paths(value), set())
        self.assertEqual(app._serialized_pcr_codex_home_paths(value), set())
        self.assertLess(time.monotonic() - started, 2.0)

    def test_preserves_user_prose_and_rebinds_executable_context(self):
        workspace = "/tmp/old/20261002_104147/workspaces/agent_001"
        record = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": workspace}]}}
        self.assertFalse(app._rebind_rollout_record_workspace(record, "/tmp/new", {workspace}, None, set()))
        self.assertEqual(record["payload"]["content"][0]["text"], workspace)
        record = {"type": "response_item", "payload": {"type": "function_call", "arguments": json.dumps({"cwd": workspace})}}
        self.assertTrue(app._rebind_rollout_record_workspace(record, "/tmp/new", set(), None, set()))
        self.assertEqual(json.loads(record["payload"]["arguments"]), {"cwd": "/tmp/new"})


class ResumePreparationTests(unittest.TestCase):
    def test_copy_rebind_and_sanitize_read_source_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            destination = root / "copy.jsonl"
            records = [
                {"type": "session_meta", "payload": {"id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "cwd": "/old"}},
                {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "kept", "input": "/old/file"}},
                {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "kept", "output": "data:image/png;base64," + "abc/def/ghi/" * 10_000}},
                {"type": "compacted", "payload": {"replacement_history": []}},
                {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "kept", "output": "orphan"}},
            ]
            original = "".join(json.dumps(record) + "\n" for record in records) + "invalid JSON\n"
            source.write_text(original)
            with mock.patch.object(app.json, "loads", wraps=json.loads) as parsed:
                changed, promoted = app._rewrite_rollout_session(destination, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", Path("/new"), promote_source=False, source_path=source)
                self.assertEqual(parsed.call_count, len(records) + 1)
            self.assertTrue(changed)
            self.assertFalse(promoted)
            self.assertEqual(source.read_text(), original)
            text = destination.read_text()
            self.assertTrue(text.endswith("invalid JSON\n"))
            copied = [json.loads(line) for line in text.splitlines()[:-1]]
            self.assertEqual(copied[0]["payload"]["cwd"], "/new")
            self.assertEqual(copied[1]["payload"]["input"], "/new/file")
            self.assertEqual(copied[2], records[2])
            self.assertEqual(len(copied), 4)
            self.assertEqual(app.sanitize_rollout_orphan_custom_tool_outputs(destination), 0)

    def test_atomic_copy_keeps_destination_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            destination = root / "copy.jsonl"
            source.write_text(json.dumps({"type": "session_meta", "payload": {"id": "child", "cwd": "/old", "source": "subagent", "parent_thread_id": "parent"}}) + "\n")
            destination.write_text("previous complete copy\n")
            before = source.read_bytes()
            with self.assertRaises(ValueError):
                app._rewrite_rollout_session(destination, "child", Path("/new"), promote_source=False, source_path=source)
            self.assertEqual(destination.read_text(), "previous complete copy\n")
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(root.glob(".*pcr-tmp*")), [])

    def test_unchanged_source_still_creates_independent_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            destination = root / "copy.jsonl"
            source.write_text('{"type":"session_meta","payload":{"id":"aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa","cwd":"/new"}}\n')
            self.assertEqual(app._rewrite_rollout_session(destination, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", Path("/new"), promote_source=False, source_path=source), (False, False))
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertNotEqual(source.stat().st_ino, destination.stat().st_ino)

    def test_prepared_home_uses_single_pass_and_independent_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_home = root / "real"
            rollout = real_home / "sessions" / "rollout-2026-01-01T00-00-00-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text('{"type":"session_meta","payload":{"id":"aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa","cwd":"/old"}}\n')
            with closing(sqlite3.connect(real_home / "state_5.sqlite")) as connection:
                connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT)")
                connection.execute("INSERT INTO threads VALUES (?, ?, ?)", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "/old", str(rollout)))
                connection.commit()
            agent_home = root / "agent"
            workspace = root / "workspace"
            workspace.mkdir()
            with mock.patch.object(app, "sanitize_rollout_orphan_custom_tool_outputs", side_effect=AssertionError("second pass")), mock.patch.object(app, "copy_file_atomic", side_effect=AssertionError("intermediate copy")):
                app.prepare_agent_codex_home(real_home, agent_home, workspace, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
            with closing(sqlite3.connect(agent_home / "state_5.sqlite")) as connection:
                row = connection.execute("SELECT cwd, rollout_path FROM threads WHERE id = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'").fetchone()
            self.assertEqual(row, (str(workspace), str(agent_home / "sessions" / rollout.name)))
            with closing(sqlite3.connect(real_home / "state_5.sqlite")) as connection:
                self.assertEqual(connection.execute("SELECT cwd FROM threads WHERE id = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'").fetchone()[0], "/old")


if __name__ == "__main__":
    unittest.main()
