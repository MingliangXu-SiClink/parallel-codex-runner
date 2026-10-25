from pathlib import Path
import tempfile
import unittest

from parallel_codex_runner_core.plugin_runtime import (
    PluginRunError,
    _read_utf8_text_page,
)


class FinalMessagePagingTests(unittest.TestCase):
    def test_every_utf8_cursor_restarts_only_when_it_splits_a_character(self):
        message = "A\u00e9\u4e2d\u6587\U0001f600\u0065\u0301\r\n"
        raw = message.encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer.md"
            path.write_bytes(raw)
            for transform in (None, lambda value: value):
                for cursor in range(len(raw) + 2):
                    with self.subTest(transformed=transform is not None, cursor=cursor):
                        page = _read_utf8_text_page(
                            path, cursor=cursor, max_bytes=4096, transform=transform
                        )
                        expected = cursor
                        if cursor > len(raw) or (
                            cursor < len(raw) and raw[cursor] & 0xC0 == 0x80
                        ):
                            expected = 0
                        self.assertEqual(page["cursor"], expected)
                        self.assertEqual(page["text"].encode("utf-8"), raw[expected:])
                        self.assertEqual(page["next_cursor"], len(raw))
                        self.assertFalse(page["has_more"])

    def test_replaced_multibyte_artifact_restarts_old_cursor(self):
        for transform in (None, lambda value: value):
            for first_message in ("a" * 4096, "a" * 8192):
                with self.subTest(transformed=transform is not None, size=len(first_message)):
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp) / "answer.md"
                        path.write_text(first_message, encoding="utf-8")
                        old = _read_utf8_text_page(
                            path, cursor=0, max_bytes=4096, transform=transform
                        )
                        # The old page boundary (or old EOF) is now inside a character.
                        message = "A" + "\u4e2d\u6587\U0001f600" * 2000
                        path.write_text(message, encoding="utf-8")
                        page = _read_utf8_text_page(
                            path, cursor=old["next_cursor"], max_bytes=4096,
                            transform=transform,
                        )
                        self.assertEqual(page["cursor"], 0)
                        self.assertNotEqual(page["message_id"], old["message_id"])
                        fragments = []
                        while True:
                            fragments.append(page["text"])
                            self.assertLessEqual(len(page["text"].encode("utf-8")), 4096)
                            if not page["has_more"]:
                                break
                            following = _read_utf8_text_page(
                                path, cursor=page["next_cursor"], max_bytes=4096,
                                transform=transform,
                            )
                            self.assertEqual(following["cursor"], page["next_cursor"])
                            self.assertEqual(following["message_id"], page["message_id"])
                            page = following
                        self.assertEqual("".join(fragments), message)

    def test_rendered_path_change_invalidates_old_eof_without_file_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer.md"
            raw = "[result](/private/result)\n\u6587\u4f3c".encode("utf-8")
            path.write_bytes(raw)
            old = _read_utf8_text_page(
                path, cursor=0, max_bytes=4096, transform=lambda value: value
            )
            visible = raw.decode("utf-8").replace("/private/", "/accepted/")
            self.assertEqual(visible.encode("utf-8")[old["next_cursor"]], 0xBC)
            restarted = _read_utf8_text_page(
                path, cursor=old["next_cursor"], max_bytes=4096,
                transform=lambda value: value.replace("/private/", "/accepted/"),
            )
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(restarted["cursor"], 0)
            self.assertNotEqual(restarted["message_id"], old["message_id"])
            self.assertEqual(restarted["text"], visible)
            eof = _read_utf8_text_page(
                path, cursor=restarted["next_cursor"], max_bytes=4096,
                transform=lambda value: value.replace("/private/", "/accepted/"),
            )
            self.assertEqual(eof["message_id"], restarted["message_id"])
            self.assertEqual(eof["text"], "")
            self.assertFalse(eof["has_more"])

    def test_aligned_stale_cursor_still_reports_changed_rendered_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer.md"
            path.write_text("abcd", encoding="utf-8")
            old = _read_utf8_text_page(
                path, cursor=0, max_bytes=4096, transform=lambda value: value
            )
            changed = _read_utf8_text_page(
                path, cursor=old["next_cursor"], max_bytes=4096,
                transform=lambda value: value + "tail",
            )
            self.assertEqual(changed["cursor"], 4)
            self.assertNotEqual(changed["message_id"], old["message_id"])
            self.assertEqual(changed["text"], "tail")

    def test_invalid_source_bytes_are_not_replaced_or_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer.md"
            for raw in (b"\xbc", b"valid\xbc", b"\xe4\xb8"):
                path.write_bytes(raw)
                for transform in (None, lambda value: value):
                    with self.subTest(raw=raw, transformed=transform is not None):
                        with self.assertRaisesRegex(PluginRunError, "not valid UTF-8"):
                            _read_utf8_text_page(
                                path, cursor=0, max_bytes=4096, transform=transform
                            )
                        self.assertEqual(path.read_bytes(), raw)
