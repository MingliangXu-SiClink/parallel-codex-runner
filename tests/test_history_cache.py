import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core import app, history_cache
from parallel_codex_runner_core.app import load_codex_session_history
from parallel_codex_runner_core.history_cache import HistoryReadCache
from parallel_codex_runner_core.visible_paths import record_session_workspace_mapping


def event(kind, **payload):
    return json.dumps({"type": "event_msg", "payload": {"type": kind, **payload}}) + "\n"


class NativeHistoryReadCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.rollout = self.home / "rollout.jsonl"
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "one"}}) + "\n"
            + event("task_started", turn_id="turn")
            + event("user_message", message="question")
            + event("agent_message", message="answer"))

    def load(self, **kwargs):
        return load_codex_session_history(self.home, "one", self.rollout, **kwargs)

    def test_cold_process_reuses_complete_projection_with_original_ids(self):
        expected = self.load()
        with mock.patch.object(app, "_read_codex_history_candidates", side_effect=AssertionError("reparsed native archive")):
            self.assertEqual(self.load(), expected)
        self.assertEqual([entry.id for entry in expected], ["history:one:2", "history:one:3"])
        path = HistoryReadCache(self.home, self.rollout, "one", None).path
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(path.parent.glob(".history-*")), [])

    def test_append_truncate_and_replacement_invalidate_cached_content(self):
        self.load()
        with self.rollout.open("a") as handle:
            handle.write(event("agent_message", message="new output"))
        self.assertIn("new output", self.load()[-1].text)
        self.rollout.write_text(event("user_message", message="replacement"))
        self.assertEqual([entry.text for entry in self.load()], ["replacement"])
        previous = self.rollout.stat()
        replacement = self.home / "replacement.jsonl"
        replacement.write_text(event("user_message", message="replacemenT"))
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        os.replace(replacement, self.rollout)
        self.assertEqual([entry.text for entry in self.load()], ["replacemenT"])

    def test_prefix_limits_and_session_identity_cannot_share_a_cache(self):
        self.load()
        self.assertEqual([entry.text for entry in self.load(max_lines=3)], ["question"])
        self.assertEqual(self.load(max_lines=0), [])
        with self.assertRaisesRegex(ValueError, "session mismatch"):
            load_codex_session_history(self.home, "another", self.rollout)
        self.rollout.unlink()
        with self.assertRaises(FileNotFoundError):
            self.load()

    def test_cache_is_raw_and_rechecks_mapping_and_link_availability(self):
        source = self.home / "old"
        accepted = self.home / "accepted"
        accepted.mkdir()
        self.rollout.write_text(event("agent_message", message=f"[result]({source}/result.txt)"))
        raw = self.load()
        record_session_workspace_mapping(self.home, "one", source, accepted)
        self.assertEqual(self.load(), raw)
        (accepted / "result.txt").write_text("fixture")
        with mock.patch.object(app, "_read_codex_history_candidates", side_effect=AssertionError("raw cache missed")):
            self.assertIn(str(accepted), self.load()[0].text)
            (accepted / "result.txt").unlink()
            self.assertEqual(self.load(), raw)

    def test_corrupt_or_unavailable_cache_cannot_block_the_original(self):
        expected = self.load()
        cache = HistoryReadCache(self.home, self.rollout, "one", None)
        for content in ["{", "[]", json.dumps({"version": 1, "identity": cache.identity,
                "fingerprint": cache.fingerprint(), "entries": [[3, {}, "", "output", "bad"]]})]:
            cache.path.write_text(content)
            self.assertEqual(self.load(), expected)
        cache.path.unlink()
        with mock.patch.object(history_cache.tempfile, "mkstemp", side_effect=PermissionError):
            self.assertEqual(self.load(), expected)
        self.assertFalse(cache.path.exists())

    def test_source_changed_during_read_is_not_published_as_fixed_history(self):
        original = app._read_codex_history_candidates
        def changed(*args):
            entries = original(*args)
            with self.rollout.open("a") as handle:
                handle.write(event("agent_message", message="late output"))
            return entries
        with mock.patch.object(app, "_read_codex_history_candidates", side_effect=changed):
            self.load()
        self.assertFalse(HistoryReadCache(self.home, self.rollout, "one", None).path.exists())
        self.assertIn("late output", self.load()[-1].text)

    def test_cache_size_limit_never_truncates_a_conversation(self):
        with mock.patch.object(history_cache, "_MAX_CACHE_BYTES", 32):
            self.assertEqual([entry.text for entry in self.load()], ["question", "answer"])
            self.assertFalse(HistoryReadCache(self.home, self.rollout, "one", None).path.exists())


if __name__ == "__main__":
    unittest.main()
