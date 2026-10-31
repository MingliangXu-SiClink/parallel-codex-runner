import asyncio
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from parallel_codex_runner_core.app import stream_to_log
from parallel_codex_runner_core.models import AgentState
from parallel_codex_runner_core.plugin_runtime import PluginRunManager, PluginRunError
from parallel_codex_runner_core.remote.transcript import transcript_items
from parallel_codex_runner_core.work_operations import WorkOperationTracker, classify_command
from parallel_codex_runner_core.work_operations import strip_shell_command_wrapper
from parallel_codex_runner_core.plugin.events import EventLog


def command(identifier="item_0", status="in_progress", output=None, code=None):
    item = {"id": identifier, "type": "command_execution", "command": "cat current.tex", "status": status}
    if output is not None:
        item["aggregated_output"] = output
    if code is not None:
        item["exit_code"] = code
    return {"type": "item.started" if status == "in_progress" else "item.completed", "item": item}


class WorkOperationTests(unittest.TestCase):
    def test_command_display_preserves_large_scripts_without_shell_tokenization(self):
        text = "/bin/zsh -lc 'python -c " + "x" * (1024 * 1024) + "'"
        payload = command()
        payload["item"]["command"] = text
        event = {"type": "agent_line", "event_id": 4, "text": json.dumps(payload)}
        with patch("shlex.split", side_effect=AssertionError("unbounded display parsing")):
            self.assertEqual(strip_shell_command_wrapper(text), text)
            item = transcript_items([event])[0]
            self.assertEqual(item["text"], text)
            self.assertEqual(item["operation_id"], "item_0")
            self.assertEqual(item["status"], "running")
            with tempfile.TemporaryDirectory() as directory:
                log = EventLog(Path(directory))
                log.append("fixture", event)
                cursor, fragments = 0, []
                while True:
                    page = log.read("fixture", cursor=cursor, limit=100)
                    fragments.extend(transcript_items(page["events"]))
                    if not page["has_more"]:
                        break
                    self.assertGreater(page["next_cursor"], cursor)
                    cursor = page["next_cursor"]
                self.assertEqual("".join(item["text"] for item in fragments), text)

    def test_small_command_wrappers_keep_their_existing_display(self):
        for source, expected in [
            ("/bin/zsh -lc 'cat a'", "cat a"),
            ("dash -c 'echo two words'", "echo two words"),
            ("/bin/bash -lc 'echo \"hello\"'", 'echo "hello"'),
            ("zsh -c 'unterminated", "zsh -c 'unterminated"),
            ("echo 'two words'", "echo 'two words'"),
            ("sh -c 'echo a' extra", "sh -c 'echo a' extra"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(strip_shell_command_wrapper(source), expected)

    def test_large_or_dynamic_commands_do_not_enter_shell_tokenization(self):
        for text in ("python -c '" + "x" * 100_000 + "'", "cat a\ncat b", "cat $HOME/a", "cat `pwd`/a"):
            with self.subTest(length=len(text)), patch(
                "parallel_codex_runner_core.work_operations.shlex.split",
                side_effect=AssertionError("display classification must stay bounded"),
            ):
                kind, paths, query = classify_command(text)
                self.assertEqual((kind.value, paths, query), ("command", [], None))

    def test_conservative_classification(self):
        for text, kind, paths in [
            ("cat 'a b.tex'", "file_read", ["a b.tex"]),
            ("/bin/zsh -lc 'sed -n 1,30p current.tex'", "file_read", ["current.tex"]),
            ("rg -n -e equation current.tex", "file_search", ["current.tex"]),
            ("rg --files src", "directory_list", ["src"]),
            ("sed -i '' s/a/b/ current.tex", "command", []),
            ("cat a | python fix.py", "command", []),
            ("cat $(find .)", "command", []),
            ("python fix.py", "command", []),
        ]:
            with self.subTest(text=text):
                actual = classify_command(text)
                self.assertEqual((actual[0].value, actual[1]), (kind, paths))

    def test_identity_survives_updates_but_not_retry_or_second_call(self):
        tracker = WorkOperationTracker()
        start = tracker.observe(command(), 1)
        end = tracker.observe(command(status="completed", code=0), 2.5)
        replay = tracker.observe(command(status="completed", code=0), 4)
        self.assertEqual(start["id"], end["id"])
        self.assertEqual((end["duration_ms"], end["duration_source"]), (1500, "observed"))
        self.assertEqual(replay["duration_ms"], 1500)
        self.assertNotEqual(start["id"], tracker.observe(command("item_1"), 5)["id"])
        self.assertNotEqual(start["id"], WorkOperationTracker().observe(command(), 6)["id"])

    def test_no_guessed_duration_and_no_matches_is_not_failure(self):
        payload = command(status="failed", code=1)
        payload["item"]["command"] = "rg missing current.tex"
        result = WorkOperationTracker().observe(payload)
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["duration_ms"])

    def test_no_fabricated_file_counts(self):
        result = WorkOperationTracker().observe({"type": "item.completed", "item": {
            "id": "patch-1", "type": "file_change", "changes": [{"kind": "add", "path": "a.tex"}]}})
        self.assertEqual(result["targets"][0]["change"], "create")
        self.assertIsNone(result["targets"][0]["added"])

    def test_stream_records_lossless_log_reference_and_compact_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stdout.log"
            path.write_bytes(b"previous attempt\n")
            events = []
            raw = (json.dumps(command(status="completed", output="line\n" * 50000)) + "\n").encode()
            async def feed():
                reader = asyncio.StreamReader()
                reader.feed_data(raw)
                reader.feed_eof()
                await stream_to_log(reader, path, AgentState(1), "stdout", events.append)
            asyncio.run(feed())
            event = next(event for event in events if event.get("type") == "agent_line")
            self.assertLess(len(json.dumps(event)), 2000)
            self.assertNotIn("aggregated_output", json.loads(event["text"])["item"])
            self.assertEqual(path.read_bytes()[event["pcr_log_offset"]:][:event["pcr_log_length"]], raw)
            self.assertEqual(event["pcr_log_sha256"], hashlib.sha256(raw).hexdigest())
            event["event_id"] = 12
            normalized = transcript_items([event])[0]
            self.assertEqual(normalized["operation"]["event_id"], 12)
            self.assertEqual(normalized["operation"]["kind"], "file_read")
            self.assertEqual(normalized["kind"], "command")

    def test_detail_uses_correct_agent_and_utf8_pages_and_rejects_replaced_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stdout.log"
            raw = (json.dumps(command(status="completed", output="中文" * 5000)) + "\n").encode()
            path.write_bytes(raw)
            event = {"event_id": 8, "idx": 1, "stream": "stdout", "pcr_log_offset": 0,
                     "pcr_log_length": len(raw), "pcr_log_sha256": hashlib.sha256(raw).hexdigest()}
            manager = SimpleNamespace(get_events=Mock(return_value={"events": [event]}),
                _lock=threading.RLock(), _require_run_locked=Mock(return_value=SimpleNamespace(agent_statuses={1: "success"}, results={})),
                _agent_meta_file=Mock(return_value=path))
            collected, cursor = "", 0
            while True:
                page = PluginRunManager.get_operation_details(manager, "run", 1, 8, cursor=cursor, max_bytes=4096)
                collected += page["text"]
                cursor = page["next_cursor"]
                self.assertNotIn("\ufffd", page["text"])
                if not page["has_more"]:
                    break
            self.assertEqual(collected, "中文" * 5000)
            with self.assertRaises(PluginRunError):
                PluginRunManager.get_operation_details(manager, "run", 2, 8)
            path.write_bytes(b"x" * len(raw))
            self.assertEqual(PluginRunManager.get_operation_details(manager, "run", 1, 8)["availability"], "removed")

    def test_legacy_events_do_not_receive_guessed_identities(self):
        item = transcript_items([{"type": "agent_line", "event_id": 2, "text": json.dumps(command())}])[0]
        self.assertNotIn("operation", item)

    def test_incomplete_tools_are_cancelled_or_unknown_not_successful(self):
        for status, expected in [("cancelled", "cancelled"), ("killed", "cancelled"), ("error", "unknown"), ("success", "unknown")]:
            tracker = WorkOperationTracker()
            started = tracker.observe(command(), 1)
            closed = tracker.finish(status)
            self.assertEqual(len(closed), 1)
            self.assertEqual((closed[0]["id"], closed[0]["status"]), (started["id"], expected))
            self.assertIsNone(closed[0]["duration_ms"])
            self.assertFalse(closed[0]["details_available"])
            self.assertEqual(tracker.finish(status), [])

    def test_completed_tools_are_not_closed_again(self):
        tracker = WorkOperationTracker()
        tracker.observe(command(), 1)
        tracker.observe(command(status="completed", code=0), 2)
        tracker.observe(command(), 3)
        self.assertEqual(tracker.finish("cancelled"), [])


if __name__ == "__main__":
    unittest.main()
