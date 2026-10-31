import json
import os
import sqlite3
import struct
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core.plugin.events import EventLog
from parallel_codex_runner_core.plugin import transcript_index
from parallel_codex_runner_core.remote.transcript import transcript_item


class ConversationIndexTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.events = EventLog(self.root)
        self.run = "pcr-test"

    def append(self, agent, text, kind="agent_message"):
        return self.events.append(self.run, {"type": "agent_line", "idx": agent,
            "text": json.dumps({"type": "item.completed", "item": {
                "id": "msg_same_item", "type": kind, "text": text,
                **({"command": text} if kind == "command_execution" else {})}})})

    def read(self, **kwargs):
        return self.events.read(self.run, transcript_kinds={"output", "warning", "error"}, **kwargs)

    def collect(self, **kwargs):
        cursor = kwargs.pop("cursor", 0)
        result = []
        for _ in range(100):
            page = self.read(cursor=cursor, **kwargs)
            result.extend(page["events"])
            if not page["has_more"]:
                return result, page
            self.assertGreater(page["next_cursor"], cursor)
            cursor = page["next_cursor"]
        self.fail("filtered read did not finish")

    def state(self):
        with closing(sqlite3.connect(self.events.conversation_index(self.run).path)) as connection:
            return connection.execute("SELECT cursor,offset FROM coverage").fetchone()

    def test_restart_and_other_agents_reuse_exact_ordinals_without_tool_reads(self):
        expected = {agent: [] for agent in range(1, 9)}
        for index in range(200):
            agent = index % 8 + 1
            self.append(agent, "tool " + "x" * 6000, "command_execution")
            if index % 3 == 0:
                expected[agent].append(self.append(agent, f"message {index}"))
        self.collect()
        self.events = EventLog(self.root)
        original = transcript_item

        def only_messages(event):
            item = original(event)
            self.assertEqual(item["kind"], "output", "Indexed read parsed an unrelated tool payload")
            return item

        with mock.patch("parallel_codex_runner_core.remote.transcript.transcript_item", side_effect=only_messages):
            for agent in range(1, 9):
                received, page = self.collect(agent=agent, limit=3)
                self.assertEqual([event["event_id"] for event in received], expected[agent])
                self.assertEqual(page["next_cursor"], self.events.count(self.run))

    def test_response_byte_limit_cannot_skip_a_later_sparse_match(self):
        first = self.append(1, "first " + "x" * 3000)
        for _ in range(100):
            self.append(2, "tool", "command_execution")
        second = self.append(1, "second " + "y" * 3000)
        self.collect()
        page = self.read(agent=1, max_bytes=4096)
        self.assertEqual([event["event_id"] for event in page["events"]], [first])
        self.assertTrue(page["has_more"])
        self.assertLessEqual(page["next_cursor"], second)
        remaining, page = self.collect(agent=1, cursor=page["next_cursor"], max_bytes=4096)
        self.assertEqual([event["event_id"] for event in remaining], [second])
        self.assertFalse(page["has_more"])

    def test_index_matches_canonical_filter_for_warnings_fragments_and_stream_updates(self):
        self.append(1, "first streamed value")
        self.append(1, "updated streamed value")
        self.append(2, "thinking", "agent_reasoning")
        self.append(2, "command", "command_execution")
        for kind in ("warning", "error", "activity", "output"):
            self.events.append(self.run, {"type": "agent_line", "idx": 2,
                "pcr_transcript_kind": kind, "pcr_transcript_text": f"fragment {kind}",
                "pcr_fragment_group": "original-group", "pcr_fragment_index": 1})
        self.events.append(self.run, {"type": "agent_line", "idx": "invalid",
            "text": "agent_message: unassigned output"})
        self.append(1, "large " + "z" * 300_000)
        raw = []
        cursor = 0
        while True:
            page = self.events.read(self.run, cursor=cursor)
            raw.extend(page["events"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        expected = [event for event in raw if (item := transcript_item(event))
                    and item["kind"] in {"output", "warning", "error"}]
        actual, page = self.collect(max_bytes=4096, limit=2)
        self.assertEqual(actual, expected)
        self.assertEqual(page["next_cursor"], len(raw))
        self.assertEqual(self.events.conversation_index(self.run).path.stat().st_mode & 0o777, 0o600)

    def test_index_budget_empty_ranges_and_late_tail_do_not_invent_coverage(self):
        for _ in range(20):
            self.append(2, "tool", "command_execution")
        tail = self.append(1, "current")
        with mock.patch.object(transcript_index, "_SCAN_RECORDS", 3):
            page = self.read(agent=1)
            self.assertEqual(page["events"], [])
            self.assertEqual(page["next_cursor"], 3)
            self.assertTrue(page["has_more"])
            recent = self.read(agent=1, cursor=tail)
            self.assertEqual([event["event_id"] for event in recent["events"]], [tail])
            self.assertFalse(recent["has_more"])
            self.assertEqual(self.state()[0], 3)
            received, page = self.collect(agent=1)
            self.assertEqual([event["event_id"] for event in received], [tail])
            self.assertEqual(page["next_cursor"], 21)

    def test_old_worker_append_and_fixed_end_cursor_keep_the_original_prefix(self):
        first = self.append(1, "first")
        self.collect()
        peer = EventLog(self.root)
        second = peer.append(self.run, {"type": "agent_line", "idx": 1, "text": "agent_message: second"})
        old, page = self.collect(end_cursor=1)
        self.assertEqual([event["event_id"] for event in old], [first])
        self.assertEqual(page["total"], 1)
        newer, page = self.collect(cursor=1)
        self.assertEqual([event["event_id"] for event in newer], [second])
        self.assertEqual(page["next_cursor"], 2)

    def test_replacement_truncation_corruption_and_rule_change_rebuild_only_the_index(self):
        for change in ("replace", "truncate", "corrupt", "rule"):
            with self.subTest(change=change):
                self.events.delete(self.run)
                self.append(1, "original")
                self.collect()
                raw = self.events.event_path(self.run).read_bytes()
                if change == "replace":
                    path = self.root / "replacement"
                    path.write_bytes(raw.replace(b"original", b"replaced"))
                    os.replace(path, self.events.event_path(self.run))
                elif change == "truncate":
                    self.events.event_path(self.run).write_bytes(b"")
                    self.append(2, "new source")
                elif change == "corrupt":
                    self.events.conversation_index(self.run).path.write_bytes(b"not a database")
                with mock.patch.object(transcript_index, "_RULE_VERSION", 2 if change == "rule" else 1):
                    received, _ = self.collect()
                    self.assertEqual(len(received), 1)
                    self.assertEqual(received[0]["event_id"], 0)
                    if change == "truncate":
                        self.assertEqual(received[0]["idx"], 2)
                    else:
                        self.assertEqual(received[0]["idx"], 1)
                if change in {"corrupt", "rule"}:
                    self.assertEqual(self.events.event_path(self.run).read_bytes(), raw)

    def test_failed_projection_transaction_cannot_publish_coverage_without_hits(self):
        self.append(1, "tool", "command_execution")
        self.collect()
        before = self.state()
        path = self.events.conversation_index(self.run).path
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TRIGGER deny_hit BEFORE INSERT ON hits BEGIN SELECT RAISE(ABORT,'fixture'); END")
        latest = self.append(1, "new output")
        page = self.read(cursor=1)
        self.assertEqual([event["event_id"] for event in page["events"]], [latest])
        self.assertEqual(self.state(), before)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("DROP TRIGGER deny_hit")
        self.collect(cursor=1)
        self.assertEqual(self.state()[0], 2)

    def test_partial_tail_is_never_indexed_and_delete_removes_derived_files(self):
        self.append(1, "complete")
        self.collect()
        with self.events.event_path(self.run).open("ab") as stream:
            stream.write(b'{"type":"agent_line"')
        received, page = self.collect()
        self.assertEqual(len(received), 1)
        self.assertEqual(page["next_cursor"], 1)
        self.assertEqual(self.state(), (1, self.events.event_path(self.run).stat().st_size))
        self.events.delete(self.run)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_indexing_single_flight_does_not_block_old_worker_or_other_run(self):
        for _ in range(10):
            self.append(1, "first")
        self.events.append("other", {"type": "status"})
        entered, release = threading.Event(), threading.Event()
        original = transcript_item
        calls = []

        def classify(event):
            if threading.current_thread().name.startswith("IndexTest"):
                calls.append(event["event_id"])
                entered.set()
                release.wait(3)
            return original(event)

        with mock.patch("parallel_codex_runner_core.remote.transcript.transcript_item", side_effect=classify):
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="IndexTest") as pool:
                first = pool.submit(self.read)
                try:
                    self.assertTrue(entered.wait(1))
                    second = pool.submit(self.read, agent=2)
                    self.assertEqual(self.events.count("other"), 1)
                    peer = EventLog(self.root)
                    peer.append(self.run, {"type": "agent_line", "idx": 2, "text": "agent_message: next"})
                finally:
                    release.set()
                self.assertTrue(first.result()["events"])
                second.result()
        received, page = self.collect(agent=2)
        self.assertEqual([event["event_id"] for event in received], [10])
        self.assertFalse(page["has_more"])

    def test_reader_observes_only_published_prefix_while_legacy_writer_holds_lock(self):
        self.append(1, "first")
        self.collect()
        peer = EventLog(self.root)
        entered, release = threading.Event(), threading.Event()
        encoded = (json.dumps({"event_id": 1, "type": "agent_line", "idx": 1,
            "text": "agent_message: second"}) + "\n").encode()
        for phase in ("half_record", "full_record", "half_offset", "published"):
            with self.subTest(phase=phase):
                # The previous read repairs any abandoned unpublished tail.
                before = self.events.count(self.run)
                entered.clear(); release.clear()
                def write():
                    with peer._locked(self.run):
                        with peer.event_path(self.run).open("ab", buffering=0) as data, \
                             peer.index_path(self.run).open("ab", buffering=0) as index:
                            offset = data.tell()
                            record = encoded.replace(b'"event_id": 1', f'"event_id": {before}'.encode())
                            data.write(record[:len(record)//2] if phase == "half_record" else record)
                            if phase in {"half_offset", "published"}:
                                value = struct.pack(">Q", offset)
                                index.write(value[:4] if phase == "half_offset" else value)
                            entered.set()
                            release.wait(5)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    writer = pool.submit(write)
                    self.assertTrue(entered.wait(1))
                    try:
                        page = pool.submit(self.read, cursor=before).result(timeout=1)
                        self.assertEqual(page["next_cursor"], before + (phase == "published"))
                        self.assertEqual(len(page["events"]), int(phase == "published"))
                        self.assertEqual(self.events.index_path(self.run).stat().st_size,
                            before * 8 + (4 if phase == "half_offset" else 8 if phase == "published" else 0))
                    finally:
                        release.set()
                    writer.result(timeout=2)
                # No future append is needed to discover a full abandoned record.
                page = self.read(cursor=before)
                self.assertEqual(page["next_cursor"], before + (phase != "half_record"))

    def test_removal_during_snapshot_rejects_old_generation(self):
        self.append(1, "old")
        with self.assertRaises(OSError):
            with self.events._read_snapshot(self.run):
                peer = EventLog(self.root)
                peer.delete(self.run)
                peer.append(self.run, {"type": "agent_line", "idx": 1, "text": "agent_message: new"})
        received, _ = self.collect()
        self.assertEqual(len(received), 1)
        self.assertIn("new", received[0]["text"])

    def test_expensive_fragment_preparation_is_outside_writer_lock(self):
        self.append(1, "existing")
        entered, release = threading.Event(), threading.Event()
        from parallel_codex_runner_core.plugin import events as module
        original = module._bounded_payloads
        def prepare(payload):
            entered.set()
            release.wait(3)
            return original(payload)
        with mock.patch.object(module, "_bounded_payloads", side_effect=prepare), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(self.append, 1, "next")
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(pool.submit(EventLog(self.root).count, self.run).result(timeout=1), 1)
            finally:
                release.set()
            writer.result(timeout=2)


if __name__ == "__main__":
    unittest.main()
