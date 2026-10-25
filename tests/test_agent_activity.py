import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from parallel_codex_runner_core.agent_activity import AgentActivityProjection
from parallel_codex_runner_core.app import stream_to_log
from parallel_codex_runner_core.codex_app_server import EventAdapter
from parallel_codex_runner_core.models import AgentState
from parallel_codex_runner_core.plugin.progress import RunProgressProjection
from parallel_codex_runner_core.plugin_runtime import PluginRunManager, PluginRunError
from parallel_codex_runner_core.remote.transcript import transcript_items
from parallel_codex_runner_core.work_operations import OperationOutputStore, WorkOperationTracker, operation_output_name


def event(kind="sleep", identifier="wait-1", state="started", duration=5_400_000,
          timestamp="2026-10-06T08:40:58+00:00", **fields):
    return {"type": "agent_line", "idx": 1, "stream": "stdout", "timestamp": timestamp,
            "text": json.dumps({"type": "item." + state,
                "item": {"id": identifier, "type": kind, "durationMs": duration, **fields}})}


class AgentActivityTests(unittest.TestCase):
    def test_wait_has_a_planned_deadline_not_a_synthetic_completion(self):
        projection = AgentActivityProjection()
        start = event()
        projection.observe(start)
        snapshot = projection.snapshot()
        self.assertEqual(snapshot["state"], "waiting")
        self.assertEqual(snapshot["wait_until"], "2026-10-06T10:10:58+00:00")
        self.assertEqual(snapshot["active_operation_count"], 0)
        for _ in range(2):
            projection.observe({"type": "agent_tokens", "idx": 1, "timestamp": "future"})
            projection.observe({**start, "stream": "stderr", "text": "catalog refresh failed"})
            projection.observe(start)
            self.assertEqual(projection.snapshot(), snapshot)
        projection.observe(event(state="completed", timestamp="2026-10-06T10:11:00+00:00"))
        self.assertEqual(projection.snapshot()["state"], "awaiting_event")
        self.assertIsNone(projection.snapshot()["wait_until"])
        projection.observe(start)
        self.assertEqual(projection.snapshot()["state"], "awaiting_event")

    def test_wait_does_not_hide_parallel_commands_or_imply_job_health(self):
        projection = AgentActivityProjection()
        projection.observe(event("command_execution", "cmd", command="train"))
        projection.observe(event())
        self.assertEqual(projection.snapshot()["state"], "executing")
        self.assertEqual(projection.snapshot()["active_operation_count"], 1)
        projection.observe(event("command_execution", "cmd", state="completed", command="train"))
        self.assertEqual(projection.snapshot()["state"], "waiting")
        self.assertNotIn("background_job_status", projection.snapshot())

    def test_invalid_wait_parameters_do_not_invent_a_deadline(self):
        for duration in (None, True, -1, "900", 10**100):
            with self.subTest(duration=duration):
                projection = AgentActivityProjection()
                projection.observe(event(duration=duration))
                self.assertEqual(projection.snapshot()["state"], "waiting")
                self.assertIsNone(projection.snapshot()["wait_until"])

    def test_saved_wait_replays_into_one_visible_operation_with_original_time(self):
        items = transcript_items([{**event(), "event_id": 10},
                                  {**event(state="completed"), "event_id": 20}])
        self.assertEqual([item["activity_type"] for item in items], ["sleep", "sleep"])
        self.assertEqual(items[0]["operation"]["id"], items[1]["operation"]["id"])
        self.assertEqual(items[0]["operation"]["wait_until"], "2026-10-06T10:10:58+00:00")
        self.assertIsNone(items[0]["operation"]["duration_ms"])
        self.assertEqual(items[1]["status"], "completed")

    def test_rebuild_and_retry_preserve_agent_and_attempt_boundaries(self):
        projection = RunProgressProjection()
        projection.apply(event())
        projection.apply({**event("command_execution", "cmd", command="test"), "idx": 2})
        self.assertEqual(projection.activities[1].snapshot()["state"], "waiting")
        self.assertEqual(projection.activities[2].snapshot()["state"], "executing")
        projection.apply({"type": "batch_created", "indices": [1]})
        self.assertNotIn(1, projection.activities)
        self.assertIn(2, projection.activities)
        projection.apply(event())
        projection.apply({"type": "agent_finished", "idx": 1, "timestamp": "end"})
        self.assertEqual(projection.activities[1].snapshot()["state"], "finished")
        self.assertIsNone(projection.activities[1].snapshot()["wait_until"])

    def test_delayed_operation_updates_cannot_reopen_completed_calls(self):
        tracker = WorkOperationTracker()
        started = json.loads(event()["text"])
        ended = json.loads(event(state="completed")["text"])
        tracker.observe(started, 1)
        result = tracker.observe(ended, 2)
        replay = tracker.observe(started, 3)
        self.assertEqual(replay["status"], "completed")
        self.assertEqual(replay["duration_ms"], result["duration_ms"])
        self.assertEqual(tracker.finish("success"), [])

    def test_turn_end_clears_active_waits_without_inventing_tool_completion(self):
        projection = AgentActivityProjection()
        projection.observe(event())
        projection.observe({"type": "agent_line", "timestamp": "ended", "stream": "stdout",
            "text": '{"type":"turn.completed"}'})
        projection.observe(event())
        self.assertEqual(projection.snapshot()["state"], "awaiting_event")
        self.assertIsNone(projection.snapshot()["wait_until"])
        projection.observe(event("command_execution", "new", command="query"))
        self.assertEqual(projection.snapshot()["state"], "executing")


class CommandOutputActivityTests(unittest.TestCase):
    def test_output_index_never_reuses_an_uncommitted_or_missing_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            sizes, streams = {}, set()
            store = OperationOutputStore(Path(tmp), sizes, streams)
            operation = {"id": "attempt:cmd"}
            self.assertEqual(store.observe({"output_delta": "first"}, operation), 5)
            path = Path(tmp) / operation_output_name(operation["id"])
            for content in (b"fir", b"first uncommitted tail"):
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(OSError):
                        store.observe({"output_delta": "next"}, operation)
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(sizes[operation["id"]], 5)

    def test_native_output_is_visible_before_completion_and_late_tail_stays_terminal(self):
        raw = []
        adapter = EventAdapter(lambda value: raw.append(copy.deepcopy(value)))
        adapter.thread_id = "thread"
        adapter.started_turn({"id": "turn"})
        command = {"id": "cmd", "type": "commandExecution", "command": "train", "status": "inProgress"}
        base = {"threadId": "thread", "turnId": "turn"}
        adapter.observe("item/started", {**base, "item": command})
        for delta in ("epoch 1\n", "epoch 1\n", "中文\n"):
            adapter.observe("item/commandExecution/outputDelta", {**base, "itemId": "cmd", "delta": delta})
        self.assertEqual([item["item"]["output_delta"] for item in raw if item["type"] == "item.updated"],
                         ["epoch 1\n", "epoch 1\n", "中文\n"])
        adapter.observe("item/completed", {**base, "item": {**command, "status": "completed", "exitCode": 0,
            "aggregatedOutput": "epoch 1\nepoch 1\n中文\n"}})
        adapter.observe("item/commandExecution/outputDelta", {**base, "itemId": "cmd", "delta": "tail\n"})
        self.assertEqual(raw[-1]["item"]["status"], "completed")
        for params in ({**base, "threadId": "other"}, {**base, "turnId": "old"}):
            count = len(raw)
            adapter.observe("item/commandExecution/outputDelta", {**params, "itemId": "cmd", "delta": "wrong"})
            self.assertEqual(len(raw), count)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state, events = AgentState(1), []

            async def feed(payloads):
                reader = asyncio.StreamReader()
                reader.feed_data(b"".join((json.dumps(item, ensure_ascii=False) + "\n").encode() for item in payloads))
                reader.feed_eof()
                await stream_to_log(reader, directory / "stdout.log", state, "stdout", events.append)

            asyncio.run(feed(raw[:5]))
            before = [e for e in events if e["type"] == "agent_line"][-1]
            self.assertEqual(before["pcr_work_operation"]["status"], "running")
            output_file = directory / operation_output_name(before["pcr_work_operation"]["id"])
            self.assertEqual(output_file.read_text(), "epoch 1\nepoch 1\n中文\n")
            asyncio.run(feed(raw[5:]))
            after = [e for e in events if e["type"] == "agent_line"][-1]
            self.assertEqual(after["pcr_work_operation"]["status"], "completed")
            self.assertEqual(output_file.read_text(), "epoch 1\nepoch 1\n中文\ntail\n")
            self.assertEqual(before["pcr_output_bytes"], len("epoch 1\nepoch 1\n中文\n".encode()))
            self.assertNotIn("output_delta", before["text"])

    def test_output_pages_read_immutable_prefixes_not_growing_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            text = "中文" * 4_000
            identity = "attempt:cmd"
            path = directory / operation_output_name(identity)
            path.parent.mkdir()
            path.write_bytes(text.encode())
            saved = {"event_id": 8, "idx": 1, "pcr_output_bytes": len(text.encode()),
                     "pcr_work_operation": {"id": identity}}
            manager = SimpleNamespace(get_events=Mock(return_value={"events": [saved]}), _lock=threading.RLock(),
                _require_run_locked=Mock(return_value=SimpleNamespace(agent_statuses={1: "running"}, results={})),
                _agent_meta_file=Mock(return_value=path))
            with path.open("ab") as target:
                target.write(b"later output")
            collected, cursor = "", 0
            while True:
                page = PluginRunManager.get_operation_details(manager, "run", 1, 8, cursor=cursor, max_bytes=4096)
                collected += page["text"]
                cursor = page["next_cursor"]
                self.assertNotIn("\ufffd", page["text"])
                self.assertEqual(page["total_bytes"], len(text.encode()))
                if not page["has_more"]:
                    break
            self.assertEqual(collected, text)
            with self.assertRaises(PluginRunError):
                PluginRunManager.get_operation_details(manager, "run", 2, 8)
            with self.assertRaises(PluginRunError):
                PluginRunManager.get_operation_details(manager, "run", 1, 8, cursor=1)
            path.write_bytes(b"")
            self.assertEqual(PluginRunManager.get_operation_details(manager, "run", 1, 8)["availability"], "removed")

    def test_meaningful_output_updates_do_not_release_an_active_wait(self):
        projection = AgentActivityProjection()
        projection.observe(event())
        sample = event("command_execution", "cmd", state="completed", command="query")
        sample["pcr_work_operation"] = {"output_bytes": 17}
        projection.observe(sample)
        self.assertEqual(projection.snapshot()["state"], "waiting")
        self.assertEqual(projection.snapshot()["last_output_at"], sample["timestamp"])

    def test_final_snapshot_then_unpositioned_delta_fails_closed_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state, events = AgentState(1), []
            async def feed():
                reader = asyncio.StreamReader()
                reader.feed_data(b'{"type":"item.completed","item":{"type":"command_execution","id":"cmd","command":"train","aggregated_output":"initial","status":"completed"}}\n')
                reader.feed_data(b'{"type":"item.updated","item":{"type":"command_execution","id":"cmd","command":"train","output_delta":"tail","status":"completed"}}\n')
                reader.feed_eof()
                await stream_to_log(reader, directory / "stdout.log", state, "stdout", events.append)
            asyncio.run(feed())
            record = [e for e in events if e["type"] == "agent_line"][-1]
            self.assertEqual((directory / operation_output_name(record["pcr_work_operation"]["id"])).read_text(), "initial")
            self.assertTrue(record["pcr_output_unavailable"])
            self.assertIn('"output_delta":"tail"', (directory / "stdout.log").read_text())
            self.assertEqual(record["pcr_work_operation"]["status"], "completed")

    def test_completion_summary_does_not_duplicate_a_late_stream_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, events = AgentState(1), []
            async def feed():
                reader = asyncio.StreamReader()
                for status, field, value in (("updated", "output_delta", "A"),
                                             ("completed", "aggregated_output", "AB"),
                                             ("updated", "output_delta", "B")):
                    reader.feed_data((json.dumps({"type": "item." + status, "item": {
                        "type": "command_execution", "id": "cmd", "command": "train", field: value,
                        "status": "completed" if status == "completed" else "in_progress"}}) + "\n").encode())
                reader.feed_eof()
                await stream_to_log(reader, Path(tmp) / "stdout.log", state, "stdout", events.append)
            asyncio.run(feed())
            record = [e for e in events if e["type"] == "agent_line"][-1]
            self.assertEqual((Path(tmp) / operation_output_name(record["pcr_work_operation"]["id"])).read_text(), "AB")
            self.assertEqual(record["pcr_work_operation"]["status"], "completed")

    def test_optional_output_index_failure_never_stops_the_agent_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            async def feed():
                reader = asyncio.StreamReader()
                reader.feed_data(b'{"type":"item.updated","item":{"type":"command_execution","id":"cmd","command":"train","output_delta":"real output","status":"in_progress"}}\n')
                reader.feed_data(b'{"type":"item.updated","item":{"type":"command_execution","id":"cmd","command":"train","output_delta":"next output","status":"in_progress"}}\n')
                reader.feed_data(b'{"type":"item.completed","item":{"type":"agent_message","id":"msg_done","text":"still working"}}\n')
                reader.feed_eof()
                await stream_to_log(reader, Path(tmp) / "stdout.log", AgentState(1), "stdout", events.append)
            with patch("parallel_codex_runner_core.app.OperationOutputStore.observe", side_effect=OSError("not writable")):
                asyncio.run(feed())
            lines = [e for e in events if e["type"] == "agent_line"]
            self.assertTrue(lines[0]["pcr_output_unavailable"])
            self.assertTrue(lines[1]["pcr_output_unavailable"])
            self.assertIn("still working", lines[-1]["text"])


if __name__ == "__main__":
    unittest.main()
