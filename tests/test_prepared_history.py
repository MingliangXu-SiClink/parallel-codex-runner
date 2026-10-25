import asyncio
import json
import sqlite3
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core import app
from parallel_codex_runner_core.models import AgentState
from parallel_codex_runner_core.plugin.progress import RunProgressProjection
from parallel_codex_runner_core.codex_model_cache import CodexModelCacheManager
from .test_codex_model_cache import write_model_cache


class PreparedHistoryTests(unittest.TestCase):
    def fixture(self, root, paginated=False):
        home = root / "source"
        path = home / "sessions" / "rollout-2026-01-01T00-00-00-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa.jsonl"
        path.parent.mkdir(parents=True)
        mode = "paginated" if paginated else "legacy"
        records = [
            {"type": "session_meta", "ordinal": 0, "payload": {"id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "cwd": "/old", "history_mode": mode}},
            {"type": "response_item", "ordinal": 1, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "/old is original user prose"}]}},
            {"type": "response_item", "ordinal": 2, "payload": {"type": "function_call", "arguments": json.dumps({"cwd": "/old", "file": "/old/a"})}},
        ]
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        with sqlite3.connect(home / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, history_mode TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, ?, ?, ?)", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "/old", str(path), mode))
        return home, path

    def test_fresh_home_does_not_copy_global_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, _ = self.fixture(root)
            with mock.patch.object(app, "copy_sqlite_database", side_effect=AssertionError("global database on fresh chat")):
                app.prepare_agent_codex_home(home, root / "private", root / "workspace", None)
            self.assertFalse((root / "private/state_5.sqlite").exists())
            self.assertEqual(app.estimate_agent_metadata_bytes(home, runtime_reserve_bytes=0), 0)

    def test_shared_source_begins_before_workspace_preparation_and_close_joins_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, _ = self.fixture(root)
            source = app.AgentCodexSource(home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", root / "batch")
            entered, finish = threading.Event(), threading.Event()
            original = source._prepare

            def prepare():
                entered.set()
                self.assertTrue(finish.wait(5))
                original()

            with mock.patch.object(source, "_prepare", side_effect=prepare):
                source.start()
                self.assertTrue(entered.wait(5))
                self.assertFalse(source._ready)
                finish.set()
                source.close()
            self.assertFalse(source.root.exists())

    def test_paged_ancestry_and_native_projection_survive_adoption_and_scrubbing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, leaf = self.fixture(root, paginated=True)
            ancestor = home / "archived_sessions/rollout-2026-01-01T00-00-00-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa_bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb.jsonl"
            ancestor.parent.mkdir()
            ancestor.write_text(json.dumps({"type": "session_meta", "ordinal": 0, "payload": {"id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "history_mode": "paginated", "cwd": "/old"}}) + "\n")
            header = json.loads(leaf.read_text().splitlines()[0])
            header["ordinal"] = 1
            header["payload"].update(history_base={"thread_id": "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb", "end_byte_offset": ancestor.stat().st_size, "end_ordinal_exclusive": 1})
            leaf.write_text(json.dumps(header) + "\n")
            with sqlite3.connect(home / "thread_history_1.sqlite") as connection:
                connection.execute("CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY, next_rollout_byte_offset INTEGER, next_rollout_ordinal INTEGER)")
                for physical, path in (("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb", ancestor), ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", leaf)):
                    connection.execute("INSERT INTO thread_history_projection_state VALUES (?, ?, 1)", (physical, path.stat().st_size))
            source = app.AgentCodexSource(home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", root / "batch")
            private = root / "agent/codex_home"
            app.prepare_agent_codex_home(home, private, root / "workspace", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
            app.scrub_codex_home_support_entries(private)
            self.assertTrue((private / "thread_history_1.sqlite").exists())
            destination = root / "adopted"
            destination.mkdir()
            app.copy_sqlite_database(home / "state_5.sqlite", destination / "state_5.sqlite")
            with mock.patch.object(app, "_rewrite_rollout_session", side_effect=AssertionError("adoption must retain native paging")):
                promoted = app.import_codex_session_to_workspace(destination, private, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", root / "workspace")
            self.assertFalse(promoted.error, promoted.error)
            source.close()
            shutil.rmtree(private)
            shutil.rmtree(home)
            adopted = app.resolve_resume_history(destination, Path(promoted.rollout_path))
            self.assertTrue(adopted.paginated)
            self.assertEqual(adopted.physical_ids, ("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"))
            self.assertTrue(all(path.path.is_relative_to(destination.resolve()) for path in adopted.segments))
            with sqlite3.connect(destination / "thread_history_1.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_history_projection_state").fetchone()[0], 2)

    def test_import_projection_replaces_only_selected_physical_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory, selected in (("candidate", 10), ("real", 99)):
                home = root / directory
                home.mkdir()
                with sqlite3.connect(home / "thread_history_1.sqlite") as connection:
                    connection.execute("CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY, next_rollout_byte_offset INTEGER)")
                    connection.executemany("INSERT INTO thread_history_projection_state VALUES (?, ?)", [("selected", selected), ("unrelated", selected + 1)])
            app.import_history_projection(root / "candidate", root / "real", ("selected",))
            with sqlite3.connect(root / "real/thread_history_1.sqlite") as connection:
                self.assertEqual(dict(connection.execute("SELECT * FROM thread_history_projection_state")), {"selected": 10, "unrelated": 100})

    def test_one_scoped_snapshot_and_parse_for_parallel_independent_legacy_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, original = self.fixture(root)
            before = original.read_bytes()
            source = app.AgentCodexSource(home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", root / "batch")

            def prepare(index):
                private = root / f"agent-{index}" / "codex_home"
                workspace = root / f'workspace-{index}-"中文\\'
                app.prepare_agent_codex_home(home, private, workspace, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
                with sqlite3.connect(private / "state_5.sqlite") as connection:
                    path = Path(connection.execute("SELECT rollout_path FROM threads").fetchone()[0])
                records = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(records[0]["payload"]["cwd"], str(workspace))
                self.assertEqual(records[1]["payload"]["content"][0]["text"], "/old is original user prose")
                self.assertEqual(json.loads(records[2]["payload"]["arguments"])["cwd"], str(workspace))
                return path, private

            with mock.patch.object(app, "copy_scoped_database", wraps=app.copy_scoped_database) as backed_up, mock.patch.object(app, "_rewrite_rollout_session", wraps=app._rewrite_rollout_session) as parsed:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    copies = list(pool.map(prepare, range(5)))
                self.assertEqual(backed_up.call_count, 1)
                self.assertEqual(parsed.call_count, 1)
            self.assertEqual(len({path.stat().st_ino for path, _ in copies}), 5)
            copies[0][0].write_text("changed")
            with sqlite3.connect(copies[0][1] / "state_5.sqlite") as connection:
                connection.execute("DELETE FROM threads")
            self.assertEqual(original.read_bytes(), before)
            self.assertNotEqual(copies[1][0].read_text(), "changed")
            with sqlite3.connect(copies[1][1] / "state_5.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 1)
            source.close()
            self.assertTrue(copies[1][0].exists())

    def test_native_paging_keeps_bytes_and_projection_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, original = self.fixture(root, paginated=True)
            with sqlite3.connect(home / "thread_history_1.sqlite") as connection:
                connection.execute("CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY, next_rollout_byte_offset INTEGER, next_rollout_ordinal INTEGER)")
                connection.execute("INSERT INTO thread_history_projection_state VALUES ('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa', ?, 3)", (original.stat().st_size,))
            source = app.AgentCodexSource(home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", root / "batch")
            private = root / "agent/codex_home"
            with mock.patch.object(app, "_rewrite_rollout_session", side_effect=AssertionError("native paging must not rewrite history")):
                app.prepare_agent_codex_home(home, private, root / "workspace", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
            with sqlite3.connect(private / "state_5.sqlite") as connection:
                path, mode = connection.execute("SELECT rollout_path, history_mode FROM threads").fetchone()
            self.assertEqual(Path(path).read_bytes(), original.read_bytes())
            self.assertEqual(mode, "paginated")
            with sqlite3.connect(private / "thread_history_1.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT next_rollout_byte_offset FROM thread_history_projection_state").fetchone()[0], original.stat().st_size)

    def test_incremental_repair_does_not_parse_or_rewrite_old_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            prefix = b"not parsed prefix\n" * 10000
            records = [{"type": "compacted", "payload": {"replacement_history": []}},
                       {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "old", "output": "late"}}]
            path.write_bytes(prefix + b"".join(json.dumps(record).encode() + b"\n" for record in records))
            size = path.stat().st_size
            with mock.patch.object(app.json, "loads", wraps=json.loads) as parsed:
                self.assertEqual(app.sanitize_rollout_orphan_custom_tool_outputs(path, start_offset=len(prefix)), 1)
                self.assertEqual(parsed.call_count, 2)
            self.assertEqual(path.stat().st_size, size)
            self.assertEqual(path.read_bytes()[:len(prefix)], prefix)
            self.assertNotIn(b"late", path.read_bytes()[len(prefix):])

    def test_ready_cache_never_waits_or_spawns_an_optional_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CodexModelCacheManager("missing-codex", root / "source", lambda *_: None, cache_root=root / "cache")
            with mock.patch.object(manager, "_version_locked", side_effect=AssertionError("version child")), mock.patch.object(manager, "_snapshot_locked", side_effect=AssertionError("prewarm")), mock.patch.object(manager, "_process_lock", side_effect=AssertionError("blocking lock")):
                self.assertFalse(manager.populate_ready(root / "private"))
                manager._client_version = "test"
                write_model_cache(root / "source/models_cache.json", "test")
                self.assertTrue(manager.populate_ready(root / "private"))
                with manager._lock:
                    self.assertFalse(manager.populate_ready(root / "other"))

    def test_phase_projection_retains_preparation_and_resets_retry(self):
        projection = RunProgressProjection()
        projection.apply({"type": "agent_phase", "idx": 1, "phase": "preparing", "phase_timings": {"history_preparation": 2.5}})
        projection.apply({"type": "agent_started", "idx": 1, "timestamp": "accepted"})
        self.assertEqual(projection.phase_timings[1]["history_preparation"], 2.5)
        projection.apply({"type": "batch_created", "indices": [1]})
        self.assertNotIn(1, projection.phase_timings)
        projection.apply({"type": "run_phase", "phase": "baseline", "phase_timings": {"baseline": 4, "bad": float("inf")}})
        self.assertEqual(projection.run_phase_timings, {"baseline": 4})

    def test_preparation_and_thread_resume_are_not_working_evidence(self):
        async def run():
            state = AgentState(idx=1)
            events = []
            reader = asyncio.StreamReader()
            for item in [{"type": "pcr.phase", "phase": "resuming"}, {"type": "thread.started", "thread_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"}]:
                reader.feed_data(json.dumps(item).encode() + b"\n")
            reader.feed_eof()
            with tempfile.TemporaryDirectory() as tmp:
                await app.stream_to_log(reader, Path(tmp) / "stdout", state, "stdout", events.append)
            self.assertFalse(state.turn_started)
            self.assertFalse(any(event["type"] == "agent_started" for event in events))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
