import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from parallel_codex_runner_core import app
from parallel_codex_runner_core.resume_history import resolve_resume_history


class ResumeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "real"
        self.sessions = self.home / "sessions"
        self.sessions.mkdir(parents=True)

    def write(self, thread, records):
        path = self.sessions / f"rollout-{thread}.jsonl"
        path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
        return path

    def metadata(self, ordinal=0, base=None, cwd="/old", mode="paginated"):
        # Codex forks can retain the root id in every segment's metadata.
        payload = {"id": "root", "session_id": "root", "cwd": cwd, "history_mode": mode}
        if base is not None:
            payload["history_base"] = base
        return {"type": "session_meta", "ordinal": ordinal, "payload": payload}

    def record(self, ordinal, text):
        return {"type": "response_item", "ordinal": ordinal, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

    def base(self, thread, path, ordinal):
        return {"thread_id": thread, "end_byte_offset": path.stat().st_size, "end_ordinal_exclusive": ordinal}

    def chain(self):
        root = self.write("root", [self.metadata(cwd="/old-root"), self.record(1, "inherited 中文")])
        middle = self.write("middle", [self.metadata(2, self.base("root", root, 2), "/old-middle"), self.record(3, "middle")])
        leaf = self.write("root_leaf", [self.metadata(4, self.base("middle", middle, 4), "/old-leaf"), self.record(5, "leaf")])
        return root, middle, leaf

    def test_materializes_full_lineage_with_one_legacy_header(self):
        paths = self.chain()
        originals = [path.read_bytes() for path in paths]
        history = resolve_resume_history(self.home, paths[-1])
        records = [json.loads(line) for line in history.lines()]
        self.assertTrue(history.paginated)
        self.assertEqual(history.size_bytes, sum(map(len, originals)))
        self.assertEqual(history.workspaces, {"/old-root", "/old-middle", "/old-leaf"})
        self.assertEqual(records[0]["payload"]["history_mode"], "legacy")
        self.assertNotIn("history_base", records[0]["payload"])
        self.assertNotIn("ordinal", records[0])
        self.assertEqual(sum(record["type"] == "session_meta" for record in records), 1)
        self.assertEqual([record["payload"]["content"][0]["text"] for record in records[1:]], ["inherited 中文", "middle", "leaf"])
        self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_inherits_only_exact_fork_prefix_not_later_parent_turns(self):
        root, middle, leaf = self.chain()
        with root.open("a") as source:
            source.write(json.dumps(self.record(2, "parent after fork")) + "\n")
        with middle.open("a") as source:
            source.write(json.dumps(self.record(4, "middle after fork")) + "\n")
        history = resolve_resume_history(self.home, leaf)
        text = "".join(history.lines())
        self.assertNotIn("after fork", text)
        self.assertIn("inherited", text)

    def test_legacy_history_is_byte_for_byte_unchanged(self):
        path = self.write("root", [self.metadata(mode="legacy"), self.record(1, "legacy")])
        history = resolve_resume_history(self.home, path)
        self.assertFalse(history.paginated)
        self.assertEqual("".join(history.lines()), path.read_text())

    def test_revert_uses_selected_physical_filename_not_stable_metadata_id(self):
        stable = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
        physical = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        old = self.sessions / f"rollout-2026-01-01T00-00-00-{stable}.jsonl"
        selected = self.sessions / f"rollout-2026-01-01T00-00-01-{stable}_{physical}.jsonl"
        header = self.metadata()
        header["payload"]["id"] = stable
        for path in (old, selected):
            path.write_text(json.dumps(header) + "\n")
        with sqlite3.connect(self.home / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, history_mode TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, ?, 'paginated')", (stable, str(selected)))
        self.assertEqual(app.find_rollout_path_for_session(self.home, stable), selected)
        self.assertEqual(resolve_resume_history(self.home, selected).physical_ids, (physical,))
        selected.unlink()
        self.assertIsNone(app.find_rollout_path_for_session(self.home, stable), "Never fall back to the pre-revert branch")

    def test_fork_identity_is_used_for_the_portable_header(self):
        root, middle, leaf = self.chain()
        history = resolve_resume_history(self.home, leaf)
        header = json.loads(next(history.lines("fork-thread")))
        self.assertEqual(header["payload"]["id"], "fork-thread")
        self.assertEqual(header["payload"]["session_id"], "root")
        self.assertEqual(json.loads(leaf.read_text().splitlines()[0])["payload"]["id"], "root")

    def test_missing_ancestor_fails_before_any_source_change(self):
        root, middle, leaf = self.chain()
        middle.unlink()
        original = leaf.read_bytes()
        with self.assertRaisesRegex(ValueError, "found 0"):
            resolve_resume_history(self.home, leaf)
        self.assertEqual(leaf.read_bytes(), original)

    def test_ambiguous_ancestor_fails_instead_of_guessing(self):
        root, middle, leaf = self.chain()
        other = self.sessions / "duplicate-middle.jsonl"
        other.write_bytes(middle.read_bytes())
        with self.assertRaisesRegex(ValueError, "found 2"):
            resolve_resume_history(self.home, leaf)

    def test_readonly_database_can_locate_source_outside_sessions(self):
        root, middle, leaf = self.chain()
        external = self.home / "external.jsonl"
        middle.rename(external)
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as connection:
            connection.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("middle", str(external)))
            connection.commit()
        self.assertEqual(resolve_resume_history(self.home, leaf).segments[1].path, external.resolve())

    def test_invalid_byte_or_ordinal_cutoff_is_rejected(self):
        for key, value in (("end_byte_offset", 1), ("end_byte_offset", 10**9), ("end_ordinal_exclusive", 200), ("end_byte_offset", True), ("thread_id", "../root")):
            with self.subTest(key=key, value=value):
                root, middle, leaf = self.chain()
                record = self.metadata(4, self.base("middle", middle, 4))
                record["payload"]["history_base"][key] = value
                leaf = self.write("root_leaf", [record])
                with self.assertRaises(ValueError):
                    resolve_resume_history(self.home, leaf)

    def test_cyclic_source_is_rejected(self):
        base = {"thread_id": "root", "end_byte_offset": 0, "end_ordinal_exclusive": 0}
        root = self.write("root", [self.metadata(base=base)])
        with self.assertRaisesRegex(ValueError, "found 0"):
            resolve_resume_history(self.home, root)

    def test_zero_cutoff_has_no_inherited_content(self):
        root = self.write("root", [self.metadata(), self.record(1, "not inherited")])
        base = {"thread_id": "root", "end_byte_offset": 0, "end_ordinal_exclusive": 0}
        leaf = self.write("leaf", [self.metadata(base=base), self.record(1, "leaf")])
        text = "".join(resolve_resume_history(self.home, leaf).lines())
        self.assertNotIn("not inherited", text)
        self.assertIn("leaf", text)

    def test_source_truncation_is_detected_when_materializing(self):
        root, middle, leaf = self.chain()
        history = resolve_resume_history(self.home, leaf)
        root.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "truncated"):
            list(history.lines())

    def test_large_last_record_cutoff_is_matched_without_full_file_read(self):
        root = self.write("root", [self.metadata(), self.record(1, "abc/" * 100_000)])
        leaf = self.write("leaf", [self.metadata(2, self.base("root", root, 2))])
        history = resolve_resume_history(self.home, leaf)
        self.assertEqual(history.segments[0].end, root.stat().st_size)

    def test_preparation_rebinds_inherited_paths_and_private_database(self):
        root, middle, leaf = self.chain()
        records = [json.loads(line) for line in root.read_text().splitlines()]
        records[1] = {"type": "response_item", "ordinal": 1, "payload": {"type": "function_call", "arguments": json.dumps({"cwd": "/old-root"})}}
        root = self.write("root", records)
        middle = self.write("middle", [self.metadata(2, self.base("root", root, 2), "/old-middle"), self.record(3, "middle")])
        leaf = self.write("root_leaf", [self.metadata(4, self.base("middle", middle, 4), "/old-leaf"), self.record(5, "leaf")])
        header, content = leaf.read_text().split("\n", 1)
        metadata = json.loads(header)
        metadata["payload"]["timestamp"] = "2026-01-01T00:00:00Z"
        session = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
        metadata["payload"]["id"] = session
        metadata["payload"]["session_id"] = session
        leaf.write_text(json.dumps(metadata) + "\n" + content)
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as connection:
            connection.execute("CREATE TABLE threads (id TEXT, cwd TEXT, rollout_path TEXT, history_mode TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, ?, ?, ?)", (session, "/old-leaf", str(leaf), "paginated"))
            connection.commit()
        private = self.home.parent / "private"
        workspace = self.home.parent / "workspace"
        workspace.mkdir()
        app.prepare_agent_codex_home(self.home, private, workspace, session)
        with closing(sqlite3.connect(private / "state_5.sqlite")) as connection:
            row = connection.execute("SELECT cwd, rollout_path, history_mode FROM threads WHERE id = ?", (session,)).fetchone()
        self.assertEqual(row[0], str(workspace))
        self.assertEqual(row[2], "legacy")
        copied = [json.loads(line) for line in Path(row[1]).read_text().splitlines()]
        self.assertEqual(json.loads(copied[1]["payload"]["arguments"])["cwd"], str(workspace))
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as connection:
            self.assertEqual(connection.execute("SELECT history_mode FROM threads").fetchone()[0], "paginated")

    def test_estimate_includes_ancestor_prefixes(self):
        root, middle, leaf = self.chain()
        expected = sum(path.stat().st_size for path in (root, middle, leaf))
        self.assertEqual(app.estimate_agent_metadata_bytes(self.home, "root", runtime_reserve_bytes=0), expected)


if __name__ == "__main__":
    unittest.main()
