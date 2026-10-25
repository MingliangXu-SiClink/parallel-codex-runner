import base64
import json
from pathlib import Path
import tempfile
import unittest

from parallel_codex_runner_core.plugin.events import EventLog
from parallel_codex_runner_core.remote.transcript import transcript_page


class NativeImageTranscriptTests(unittest.TestCase):
    def test_native_image_generation_preserves_its_envelope_at_any_size(self):
        for size in (20, 600_000):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                events = EventLog(Path(directory))
                image = base64.b64encode(b"x" * size).decode()
                payload = {"type": "item.completed", "item": {"type": "imageGeneration",
                    "id": "native-image", "status": "completed", "result": image}}
                raw = json.dumps(payload, ensure_ascii=False)
                events.append("run", {"type": "agent_line", "idx": 1, "stream": "stdout", "text": raw})
                cursor = 0
                parts = []
                for _ in range(100):
                    page = events.read("run", cursor=cursor, limit=2, max_bytes=256 * 1024)
                    projected = transcript_page(page)
                    for item in projected["items"]:
                        self.assertEqual(item["kind"], "activity")
                        self.assertEqual(item["activity_type"], "native_image_source")
                        parts.append(item["text"])
                    if not page["has_more"]:
                        break
                    self.assertGreater(page["next_cursor"], cursor)
                    cursor = page["next_cursor"]
                self.assertEqual(json.loads("".join(parts)), payload)

    def test_normal_tool_output_and_base64_are_not_reclassified_as_media(self):
        text = base64.b64encode(b"ordinary text").decode()
        page = transcript_page({"events": [{"type": "agent_line", "event_id": 3,
            "text": json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": text}})}]})
        self.assertEqual(page["items"][0]["kind"], "output")
        self.assertEqual(page["items"][0]["text"], text)


if __name__ == "__main__":
    unittest.main()
