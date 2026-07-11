from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from parallel_codex_runner_core import tui_textual as _tui_bootstrap  # noqa: F401
import textual
from textual._cells import cell_width_to_column_index
from textual.app import App, ComposeResult
from textual.drivers.linux_driver import (
    KITTY_DISAMBIGUATE_ESCAPE_CODES,
    KITTY_REPORT_ALL_KEYS,
    KITTY_REPORT_ASSOCIATED_TEXT,
    _get_kitty_protocol_flags,
)
from textual.widgets import Input, TextArea


class TextEditorApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Input(id="input")
        yield TextArea(id="text-area")


class VendoredTextualTests(unittest.IsolatedAsyncioTestCase):
    def test_repository_uses_vendored_textual(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        vendored_source = repository / "vendor" / "textual" / "src"
        self.assertTrue(Path(textual.__file__).resolve().is_relative_to(vendored_source))
        self.assertEqual(textual.__version__, "8.2.8+pcr.1")

    def test_mouse_columns_never_land_inside_a_grapheme(self) -> None:
        text = "你e\u0301好"
        self.assertEqual(cell_width_to_column_index(text, 1, 4), 0)
        self.assertEqual(cell_width_to_column_index(text, 2, 4), 1)
        self.assertEqual(cell_width_to_column_index(text, 3, 4), 3)

    def test_iterm_uses_ime_safe_keyboard_protocol(self) -> None:
        with patch.dict(
            "os.environ",
            {"LC_TERMINAL": "iTerm2", "TERM_PROGRAM": "iTerm.app"},
            clear=True,
        ):
            self.assertEqual(
                _get_kitty_protocol_flags(), KITTY_DISAMBIGUATE_ESCAPE_CODES
            )

    def test_other_terminals_keep_full_keyboard_protocol(self) -> None:
        with patch.dict("os.environ", {"TERM_PROGRAM": "WezTerm"}, clear=True):
            self.assertEqual(
                _get_kitty_protocol_flags(),
                KITTY_DISAMBIGUATE_ESCAPE_CODES
                | KITTY_REPORT_ALL_KEYS
                | KITTY_REPORT_ASSOCIATED_TEXT,
            )

    async def test_text_area_moves_and_deletes_whole_graphemes(self) -> None:
        async with TextEditorApp().run_test() as pilot:
            text_area = pilot.app.query_one("#text-area", TextArea)
            text_area.text = "e\u0301中文"
            text_area.cursor_location = (0, 2)

            text_area.action_cursor_left()
            self.assertEqual(text_area.cursor_location, (0, 0))

            text_area.action_delete_right()
            self.assertEqual(text_area.text, "中文")

    async def test_text_area_accepts_committed_chinese_input(self) -> None:
        async with TextEditorApp().run_test() as pilot:
            text_area = pilot.app.query_one("#text-area", TextArea)
            text_area.focus()
            await pilot.press("你", "好")
            self.assertEqual(text_area.text, "你好")
            self.assertEqual(text_area.cursor_location, (0, 2))

    async def test_cjk_word_actions_advance_one_character(self) -> None:
        async with TextEditorApp().run_test() as pilot:
            text_area = pilot.app.query_one("#text-area", TextArea)
            text_area.text = "你好世界"
            text_area.cursor_location = (0, 4)
            text_area.action_cursor_word_left()
            self.assertEqual(text_area.cursor_location, (0, 3))
            text_area.action_delete_word_left()
            self.assertEqual(text_area.text, "你好界")

            input_widget = pilot.app.query_one("#input", Input)
            input_widget.value = "你好世界"
            input_widget.action_end()
            input_widget.action_delete_left_word()
            self.assertEqual(input_widget.value, "你好世")


if __name__ == "__main__":
    unittest.main()
