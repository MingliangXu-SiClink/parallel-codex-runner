import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core import app
from parallel_codex_runner_core.history_origins import RECOVERY_CONTENT_KIND, RECOVERY_INPUT, RECOVERY_ORIGIN
from parallel_codex_runner_core.plugin_runtime import _history_payloads


def record(kind, **payload):
    return json.dumps({"type": kind, "payload": payload}) + "\n"


def message(role, text, turn, recovery=False):
    return record("response_item", type="message", role=role,
        content=[{"type": "input_text", "text": text}],
        internal_chat_message_metadata_passthrough={"turn_id": turn,
            "content_item_kinds": [RECOVERY_CONTENT_KIND if recovery else role + ".text"]})


class HistoryOriginTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.path = self.home / "rollout.jsonl"

    def load(self):
        return app.load_codex_session_history(self.home, "original", self.path)

    def test_native_control_is_retained_with_provenance_and_real_identical_input_is_visible(self):
        self.path.write_text(record("session_meta", id="original")
            + record("event_msg", type="task_started", turn_id="recovery")
            + message("developer", "Application recovery context", "recovery", recovery=True)
            + message("user", RECOVERY_INPUT, "recovery")
            + record("event_msg", type="user_message", message=RECOVERY_INPUT)
            + message("assistant", "Continued original computation", "recovery")
            + message("user", RECOVERY_INPUT, "recovery")
            + message("user", RECOVERY_INPUT, "human-next-turn"))
        entries = self.load()
        self.assertEqual([entry.input_origin for entry in entries], [RECOVERY_ORIGIN, "", "", ""])
        self.assertEqual([entry.id for entry in entries],
            ["history:original:3", "history:original:5", "history:original:6", "history:original:7"])
        self.assertEqual(_history_payloads(entries)[0]["input_origin"], RECOVERY_ORIGIN)
        self.assertNotIn("input_origin", _history_payloads(entries)[2])
        with mock.patch.object(app, "_read_codex_history_candidates", side_effect=AssertionError("reparsed")):
            self.assertEqual(entries, self.load())

    def test_repeated_legacy_recoveries_use_matching_original_owner_not_sticky_context(self):
        workspace = self.home / "old-run/workspaces/agent_008"
        meta = self.home / "old-run/meta/agent_008"
        meta.mkdir(parents=True)
        receipt = {"version": 1, "thread_id": "original", "identity": "original-execution"}
        (meta / "execution_recovery.json").write_text(json.dumps(receipt))
        events = [{"type": "thread.started", "thread_id": "original"}]
        native = record("session_meta", id="original", cwd=str(workspace))
        native += message("user", "Initial human task", "first")
        for index in range(6):
            turn = f"recovery-{index}"
            events.extend([
                {"type": "pcr.recovery", "thread_id": "original", "turn_id": f"failed-{index}", "attempt": index + 1},
                {"type": "turn.started", "turn_id": turn},
            ])
            native += record("turn_context", cwd=str(workspace))
            if index == 0:
                native += message("developer", "One-time context", turn, recovery=True)
            native += message("user", RECOVERY_INPUT, turn)
        native += message("user", RECOVERY_INPUT, "human")
        (meta / "stdout.log").write_text("\n".join(map(json.dumps, events)) + "\n")
        self.path.write_text(native)
        entries = self.load()
        self.assertEqual(sum(entry.input_origin == RECOVERY_ORIGIN for entry in entries), 6)
        self.assertEqual(entries[-1].input_origin, "")
        self.assertEqual(len(entries), 8)

    def test_unknown_mismatched_or_ambiguous_inputs_are_not_hidden(self):
        self.path.write_text(message("user", RECOVERY_INPUT, "human")
            + message("developer", "Not a recovery context", "other")
            + message("user", RECOVERY_INPUT, "other"))
        self.assertTrue(all(not entry.input_origin for entry in self.load()))

    def test_legacy_owner_receipt_must_match_session(self):
        workspace = self.home / "old-run/workspaces/agent_008"
        meta = self.home / "old-run/meta/agent_008"
        meta.mkdir(parents=True)
        (meta / "execution_recovery.json").write_text(json.dumps(
            {"version": 1, "thread_id": "someone-else", "identity": "not-ours"}))
        (meta / "stdout.log").write_text(json.dumps({"type": "thread.started", "thread_id": "original"}) + "\n"
            + json.dumps({"type": "turn.started", "turn_id": "one", "input_origin": RECOVERY_ORIGIN}) + "\n")
        self.path.write_text(record("session_meta", id="original", cwd=str(workspace))
            + message("user", RECOVERY_INPUT, "one"))
        self.assertEqual(self.load()[0].input_origin, "")


if __name__ == "__main__":
    unittest.main()
