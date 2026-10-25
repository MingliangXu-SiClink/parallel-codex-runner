from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._vendor import activate_textual
from .app import (
    LARGE_RUN_STORAGE_WARNING_BYTES,
    RunStorageEstimate,
    format_storage_bytes,
)
from .models import AGENT_ROLE_CANDIDATE, CodexHistoryEntry
from .tui_common import normalize_prompt_text, split_command_detail

activate_textual()

from textual import events, on
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Grid, Vertical, VerticalScroll
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static, TextArea

@dataclass
class AgentPane:
    idx: int
    role: str = AGENT_ROLE_CANDIDATE
    status: str = "idle"
    rejected: bool = False
    stale_synthesis: bool = False
    reasoning_tokens: int | None = None
    reasoning_token_counts: dict[int, int] = field(default_factory=dict)
    input_text: str = ""
    execution_prompt: str = ""
    developer_instructions: str = ""
    final_text: str = ""
    result: dict[str, Any] | None = None
    attempt_history: list[tuple[str, str, str]] = field(default_factory=list)
    detail_events: list[tuple[str, str]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    thought_lines: list[str] = field(default_factory=list)
    output_lines: list[str] = field(default_factory=list)
    show_diff: bool = False
    diff_loading: bool = False
    diff_text: str = ""
    diff_error: str = ""
    diff_request: int = 0
    revision: int = 0

    def append(self, text: str, category: str = "activity") -> None:
        text = text.strip()
        if not text:
            return
        category = category if category in {"thought", "output", "command"} else "activity"
        if not self.detail_events:
            self.detail_events.extend(("thought", line) for line in self.thought_lines)
            self.detail_events.extend(("output", line) for line in self.output_lines)
            self.detail_events.extend(("activity", line) for line in self.lines)
        bucket = self.thought_lines if category == "thought" else self.output_lines if category == "output" else self.lines
        if bucket is self.lines and "\n" in text:
            for index in range(len(bucket) - 1, -1, -1):
                if text.startswith(f"{bucket[index]}\n"):
                    previous = bucket[index]
                    bucket[index] = text
                    for event_index in range(len(self.detail_events) - 1, -1, -1):
                        if self.detail_events[event_index] == (category, previous):
                            self.detail_events[event_index] = (category, text)
                            break
                    return
        bucket.append(text)
        self.detail_events.append((category, text))

    def ordered_detail_events(self) -> list[tuple[str, str]]:
        if self.detail_events:
            return self.detail_events
        return [
            *(("thought", line) for line in self.thought_lines),
            *(("output", line) for line in self.output_lines),
            *(("activity", line) for line in self.lines),
        ]

    def has_agent_text(self) -> bool:
        return bool(self.thought_lines or self.output_lines)

    def has_active_command(self) -> bool:
        return any(
            category == "command" and not split_command_detail(text)[1]
            for category, text in self.detail_events
        )

    def clear_detail(self) -> None:
        self.diff_request += 1
        self.attempt_history.clear()
        self.detail_events.clear()
        self.lines.clear()
        self.thought_lines.clear()
        self.output_lines.clear()
        self.show_diff = False
        self.diff_loading = False
        self.diff_text = ""
        self.diff_error = ""


@dataclass
class CandidateBatch:
    indices: list[int]
    retry_indices: set[int] = field(default_factory=set)
    role: str = AGENT_ROLE_CANDIDATE
    prompt: str = ""
    developer_instructions: str = ""
    synthesis_revision: int | None = None
    synthesis_source_indices: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class QueuedFollowUp:
    prompt: str
    record_history: bool = False
    context: tuple[str, str] | None = None
    recorded_history_context: tuple[str, str] | None = None


@dataclass(frozen=True)
class QueueEditorRow:
    source_position: int
    prompt: str


@dataclass(frozen=True)
class QueueEditorResult:
    rows: tuple[QueueEditorRow, ...]


class RunnerEvent(Message):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload


class ResumeHistoryLoaded(Message):
    def __init__(
        self,
        request_id: int,
        session_id: str,
        entries: list[CodexHistoryEntry],
        error: str = "",
        rejected: bool = False,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.session_id = session_id
        self.entries = entries
        self.error = error
        self.rejected = rejected


class ResumeChoicesLoaded(Message):
    def __init__(
        self,
        request_id: int,
        workspace: Path,
        entries: list[Any],
        error: str = "",
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.workspace = workspace
        self.entries = entries
        self.error = error


class StoragePreflightFinished(Message):
    def __init__(
        self,
        request_id: int,
        prompt: str,
        record_history: bool,
        run_base: Path,
        estimate: RunStorageEstimate | None = None,
        error: str = "",
        reclaimable_bytes: int = 0,
        configuration_key: tuple[Any, ...] = (),
        from_follow_up_queue: bool = False,
        bypass_follow_up_queue: bool = False,
        recorded_history_context: tuple[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.prompt = prompt
        self.record_history = record_history
        self.run_base = run_base
        self.estimate = estimate
        self.error = error
        self.reclaimable_bytes = max(0, int(reclaimable_bytes))
        self.configuration_key = configuration_key
        self.from_follow_up_queue = from_follow_up_queue
        self.bypass_follow_up_queue = bypass_follow_up_queue
        self.recorded_history_context = recorded_history_context


class SafePasteTextArea(TextArea):
    """A plain-text editor with safe paste and native editing shortcuts."""

    @staticmethod
    def _consume_editing_key(event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    def _copy_editor_selection(self) -> None:
        if self.selected_text:
            self.app.copy_to_clipboard(self.selected_text)
            return
        copy_active = getattr(self.app, "_copy_active_text", None)
        if callable(copy_active) and copy_active():
            return

    def _sync_macos_editor_selection(self) -> None:
        # macOS terminal apps consume Command-C before Textual can receive
        # super+c. Their native Copy command cannot see TextArea's internal
        # selection, so keep the pasteboard synchronized as it is selected.
        if sys.platform == "darwin" and self.selected_text:
            self.app.copy_to_clipboard(self.selected_text)

    def action_select_all(self) -> None:
        clear_selection = getattr(
            self.app,
            "_clear_screen_selection_for_interaction",
            None,
        )
        if callable(clear_selection):
            clear_selection()
        else:
            self.app.clear_selection()
        super().action_select_all()

    def _move_to_line_edge(self, *, end: bool, select: bool) -> None:
        row, _column = self.cursor_location
        column = len(self.document[row]) if end else 0
        self.move_cursor((row, column), select=select)

    def _move_to_document_edge(self, *, end: bool, select: bool) -> None:
        self.move_cursor(self.document.end if end else (0, 0), select=select)

    def _delete_to_line_edge(self, *, end: bool) -> None:
        if not self.selection.is_empty:
            self._delete_via_keyboard(*self.selection)
            return
        row, column = self.cursor_location
        edge = len(self.document[row]) if end else 0
        if column != edge:
            self._delete_via_keyboard((row, column), (row, edge))

    def move_cursor(
        self,
        location: tuple[int, int],
        select: bool = False,
        center: bool = False,
        record_width: bool = True,
    ) -> None:
        super().move_cursor(location, select, center, record_width)
        if select:
            self._sync_macos_editor_selection()

    async def _on_key(self, event: events.Key) -> None:
        tokens = event.key.split("+")
        key = tokens[-1]
        modifiers = frozenset(tokens[:-1])

        if modifiers == {"super"} and key == "a":
            self._consume_editing_key(event)
            self.action_select_all()
            return
        if modifiers == {"super"} and key == "c":
            self._consume_editing_key(event)
            self._copy_editor_selection()
            return
        if modifiers == {"super"} and key == "x":
            self._consume_editing_key(event)
            if self.selected_text:
                self.action_cut()
            return
        if modifiers == {"super"} and key == "v":
            # Terminal emulators send the clipboard as a separate bracketed
            # Paste event. Consume the key without inserting from the app
            # clipboard, otherwise the same payload is inserted twice.
            self._consume_editing_key(event)
            return
        if modifiers in ({"super"}, {"ctrl"}) and key == "z":
            self._consume_editing_key(event)
            self.action_undo()
            return
        if (
            modifiers in ({"shift", "super"}, {"ctrl", "shift"})
            and key == "z"
        ) or (modifiers in ({"super"}, {"ctrl"}) and key == "y"):
            self._consume_editing_key(event)
            self.action_redo()
            return

        if key in {"left", "right"} and modifiers in (
            {"alt"},
            {"alt", "shift"},
        ):
            self._consume_editing_key(event)
            action = (
                self.action_cursor_word_left
                if key == "left"
                else self.action_cursor_word_right
            )
            action(select="shift" in modifiers)
            return
        if key in {"left", "right"} and modifiers in (
            {"super"},
            {"shift", "super"},
        ):
            self._consume_editing_key(event)
            self._move_to_line_edge(
                end=key == "right",
                select="shift" in modifiers,
            )
            return
        if key in {"up", "down"} and modifiers in (
            {"super"},
            {"shift", "super"},
        ):
            self._consume_editing_key(event)
            self._move_to_document_edge(
                end=key == "down",
                select="shift" in modifiers,
            )
            return

        if modifiers == {"alt"} and key in {"backspace", "delete"}:
            self._consume_editing_key(event)
            action = (
                self.action_delete_word_left
                if key == "backspace"
                else self.action_delete_word_right
            )
            action()
            return
        if modifiers == {"super"} and key in {"backspace", "delete"}:
            self._consume_editing_key(event)
            self._delete_to_line_edge(end=key == "delete")
            return

        await super()._on_key(event)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        # Screen selections (used by Detail and the runner panel) and the
        # TextArea's editable selection are independent.  Clear the former
        # before starting an editor selection so Copy cannot pick up stale
        # rendered text instead of the selected prompt lines.
        clear_selection = getattr(
            self.app,
            "_clear_screen_selection_for_interaction",
            None,
        )
        if callable(clear_selection):
            clear_selection()
        else:
            self.app.clear_selection()
        event.prevent_default()
        await super()._on_mouse_down(event)

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        event.prevent_default()
        await super()._on_mouse_up(event)
        self._sync_macos_editor_selection()

    async def _on_paste(self, event: events.Paste) -> None:
        # Textual dispatches private event handlers through the widget MRO.
        # Without preventing the default handler, TextArea._on_paste inserts
        # the same bracketed-paste payload a second time.
        event.prevent_default()
        if self.read_only:
            return
        text = normalize_prompt_text(event.text)
        if result := self._replace_via_keyboard(text, *self.selection):
            self.move_cursor(result.end_location)
            self.focus()


class StorageWarningScreen(ModalScreen[bool]):
    CSS = """
    StorageWarningScreen {
        align: center middle;
    }
    #storage-warning-dialog {
        width: 76;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        background: #171d25;
        border: round #e5b567;
    }
    #storage-warning-title {
        height: 1;
        margin-bottom: 1;
        color: #ffd27a;
        text-style: bold;
    }
    #storage-warning-body {
        height: auto;
        margin-bottom: 1;
        color: #e7ebf2;
    }
    #storage-warning-actions {
        height: 3;
        grid-size: 2 1;
        grid-columns: 1fr 1fr;
        grid-gutter: 0 1;
    }
    #storage-warning-actions Button {
        width: 100%;
    }
    """

    BINDINGS = [
        ("escape", "cancel_run", "Cancel"),
        ("n", "cancel_run", "Cancel"),
        ("y", "continue_run", "Continue"),
    ]

    def __init__(self, estimate: RunStorageEstimate) -> None:
        super().__init__()
        self.estimate = estimate

    def compose(self) -> ComposeResult:
        estimate = self.estimate
        body = "\n".join(
            (
                f"本次运行预计占用 {format_storage_bytes(estimate.total_bytes)}，"
                f"超过 {format_storage_bytes(LARGE_RUN_STORAGE_WARNING_BYTES)} 提醒阈值。",
                "",
                f"WORKSPACE COPIES  {format_storage_bytes(estimate.workspace_copies_bytes)}",
                f"META + RESERVE    {format_storage_bytes(estimate.metadata_bytes)}",
                f"AGENTS            {estimate.num_agents}",
                "",
                "继续后将检查目标磁盘的可用空间。目前尚未创建任何副本。",
            )
        )
        with Vertical(id="storage-warning-dialog"):
            yield Static("LARGE WORKSPACE", id="storage-warning-title")
            yield Static(body, id="storage-warning-body", markup=False)
            with Grid(id="storage-warning-actions"):
                yield Button("取消", variant="primary", id="storage-cancel")
                yield Button("继续", variant="warning", id="storage-continue")

    def on_mount(self) -> None:
        self.query_one("#storage-cancel", Button).focus()

    @on(Button.Pressed)
    def _on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "storage-continue")

    def action_cancel_run(self) -> None:
        self.dismiss(False)

    def action_continue_run(self) -> None:
        self.dismiss(True)


class ExitConfirmationScreen(ModalScreen[str | None]):
    CSS = """
    ExitConfirmationScreen {
        align: center middle;
    }
    #exit-confirm-dialog {
        width: 76;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        background: #171d25;
        border: round #e5b567;
    }
    #exit-confirm-title {
        height: 1;
        margin-bottom: 1;
        color: #ffd27a;
        text-style: bold;
    }
    #exit-confirm-body {
        height: auto;
        margin-bottom: 1;
        color: #e7ebf2;
    }
    #exit-confirm-actions {
        height: auto;
        grid-size: 1 3;
        grid-gutter: 0 1;
    }
    #exit-confirm-actions Button {
        width: 100%;
    }
    """

    BINDINGS = [
        ("escape", "cancel_exit", "Cancel"),
    ]

    def __init__(self, message: str, buttons: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.message = message
        self.buttons = buttons

    def compose(self) -> ComposeResult:
        with Vertical(id="exit-confirm-dialog"):
            yield Static("EXIT CONFIRMATION", id="exit-confirm-title")
            yield Static(self.message, id="exit-confirm-body", markup=False)
            with Grid(id="exit-confirm-actions"):
                for label, action, variant in self.buttons:
                    yield Button(label, variant=variant, id=f"exit-btn-{action}")

    def on_mount(self) -> None:
        first_btn = self.query_one("#exit-confirm-actions Button", Button)
        first_btn.focus()

    @on(Button.Pressed)
    def _on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        action = btn_id.removeprefix("exit-btn-")
        self.dismiss(action)

    def action_cancel_exit(self) -> None:
        self.dismiss(None)


class QueueEditorScreen(ModalScreen[QueueEditorResult | None]):
    CSS = """
    QueueEditorScreen {
        align: center middle;
    }
    #queue-editor-dialog {
        width: 92%;
        max-width: 110;
        height: 80%;
        min-height: 20;
        max-height: 38;
        padding: 1 2;
        background: #171d25;
        border: round #5aa9bd;
    }
    #queue-editor-title {
        height: 1;
        margin-bottom: 1;
        color: #c8edf5;
        text-style: bold;
    }
    #queue-editor-select {
        height: 3;
        margin-bottom: 1;
    }
    #queue-editor-prompt {
        height: 1fr;
        min-height: 8;
        margin-bottom: 1;
        border: round #4c5f74;
        background: #0d1117;
    }
    #queue-editor-status {
        height: 1;
        margin-bottom: 1;
        color: #f0c674;
    }
    #queue-editor-actions {
        height: 3;
        grid-size: 5 1;
        grid-columns: 1fr 1fr 1fr 1fr 1fr;
        grid-gutter: 0 1;
    }
    #queue-editor-actions Button {
        width: 100%;
    }
    """

    BINDINGS = [
        ("escape", "cancel_editor", "Cancel"),
        ("ctrl+s", "apply_editor", "Apply"),
        ("alt+up", "move_up", "Move up"),
        ("alt+down", "move_down", "Move down"),
    ]

    def __init__(
        self,
        rows: list[QueueEditorRow],
        selected_position: int = 1,
    ) -> None:
        super().__init__()
        self.rows = list(rows)
        source_positions = {row.source_position for row in self.rows}
        self.selected_source_position = (
            selected_position
            if selected_position in source_positions
            else self.rows[0].source_position
            if self.rows
            else None
        )

    def _selected_index(self) -> int | None:
        for index, row in enumerate(self.rows):
            if row.source_position == self.selected_source_position:
                return index
        return None

    def _select_options(self) -> list[tuple[str, int]]:
        return [
            (
                f"{index}. {' '.join(row.prompt.split())}",
                row.source_position,
            )
            for index, row in enumerate(self.rows, 1)
        ]

    def compose(self) -> ComposeResult:
        options = self._select_options()
        select_options = options or [("Queue is empty", -1)]
        select_value = (
            self.selected_source_position
            if self.selected_source_position is not None
            else -1
        )
        with Vertical(id="queue-editor-dialog"):
            yield Static("QUEUED FOLLOW-UPS", id="queue-editor-title")
            yield Select(
                select_options,
                value=select_value,
                allow_blank=False,
                compact=True,
                id="queue-editor-select",
            )
            yield SafePasteTextArea(
                "",
                id="queue-editor-prompt",
                soft_wrap=True,
                show_line_numbers=False,
            )
            yield Static("", id="queue-editor-status", markup=False)
            with Grid(id="queue-editor-actions"):
                yield Button("↑", id="queue-editor-up")
                yield Button("↓", id="queue-editor-down")
                yield Button("REMOVE", variant="error", id="queue-editor-remove")
                yield Button("CANCEL", id="queue-editor-cancel")
                yield Button("APPLY", variant="primary", id="queue-editor-apply")

    def on_mount(self) -> None:
        self._load_selected_prompt()
        self._update_controls()

    def _set_status(self, text: str) -> None:
        self.query_one("#queue-editor-status", Static).update(text)

    def _store_selected_prompt(self) -> bool:
        index = self._selected_index()
        if index is None:
            return True
        prompt = normalize_prompt_text(
            self.query_one("#queue-editor-prompt", SafePasteTextArea).text
        ).strip()
        if not prompt:
            self._set_status("A queued question cannot be empty.")
            return False
        current = self.rows[index]
        self.rows[index] = QueueEditorRow(
            current.source_position,
            prompt,
        )
        self._set_status("")
        return True

    def _load_selected_prompt(self) -> None:
        editor = self.query_one("#queue-editor-prompt", SafePasteTextArea)
        index = self._selected_index()
        if index is None:
            editor.text = ""
            editor.disabled = True
            return
        editor.disabled = False
        editor.text = self.rows[index].prompt
        lines = editor.text.split("\n")
        editor.move_cursor((len(lines) - 1, len(lines[-1])))
        editor.focus()

    def _refresh_selector(self) -> None:
        selector = self.query_one("#queue-editor-select", Select)
        options = self._select_options()
        with selector.prevent(Select.Changed):
            selector.set_options(options or [("Queue is empty", -1)])
            selector.disabled = not options
            selector.value = (
                self.selected_source_position
                if self.selected_source_position is not None
                else -1
            )

    def _update_controls(self) -> None:
        index = self._selected_index()
        has_selection = index is not None
        self.query_one("#queue-editor-up", Button).disabled = (
            not has_selection or index == 0
        )
        self.query_one("#queue-editor-down", Button).disabled = (
            not has_selection or index == len(self.rows) - 1
        )
        self.query_one("#queue-editor-remove", Button).disabled = (
            not has_selection
        )

    def _move_selected(self, offset: int) -> None:
        index = self._selected_index()
        if index is None:
            return
        destination = index + offset
        if destination < 0 or destination >= len(self.rows):
            return
        if not self._store_selected_prompt():
            return
        self.rows[index], self.rows[destination] = (
            self.rows[destination],
            self.rows[index],
        )
        self._refresh_selector()
        self._load_selected_prompt()
        self._update_controls()

    def _remove_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        self.rows.pop(index)
        if self.rows:
            next_index = min(index, len(self.rows) - 1)
            self.selected_source_position = self.rows[next_index].source_position
        else:
            self.selected_source_position = None
            self._set_status("Apply to cancel all queued questions.")
        self._refresh_selector()
        self._load_selected_prompt()
        self._update_controls()

    @on(Select.Changed, "#queue-editor-select")
    def _on_queue_item_changed(self, event: Select.Changed) -> None:
        value = event.value
        if not isinstance(value, int) or value < 1:
            return
        if value == self.selected_source_position:
            return
        previous = self.selected_source_position
        if not self._store_selected_prompt():
            self.selected_source_position = previous
            self._refresh_selector()
            return
        self.selected_source_position = value
        self._refresh_selector()
        self._load_selected_prompt()
        self._update_controls()

    @on(Button.Pressed)
    def _on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "queue-editor-up":
            self.action_move_up()
        elif button_id == "queue-editor-down":
            self.action_move_down()
        elif button_id == "queue-editor-remove":
            self._remove_selected()
        elif button_id == "queue-editor-cancel":
            self.action_cancel_editor()
        elif button_id == "queue-editor-apply":
            self.action_apply_editor()

    def action_move_up(self) -> None:
        self._move_selected(-1)

    def action_move_down(self) -> None:
        self._move_selected(1)

    def action_cancel_editor(self) -> None:
        self.dismiss(None)

    def action_apply_editor(self) -> None:
        if self.rows and not self._store_selected_prompt():
            return
        self.dismiss(QueueEditorResult(tuple(self.rows)))


class AgentDiffLoaded(Message):
    def __init__(
        self,
        idx: int,
        request_id: int,
        text: str = "",
        error: str = "",
    ) -> None:
        super().__init__()
        self.idx = idx
        self.request_id = request_id
        self.text = text
        self.error = error


class PromptSubmitted(Message):
    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = normalize_prompt_text(value)


class PromptHistoryRequested(Message):
    def __init__(self, direction: int) -> None:
        super().__init__()
        self.direction = direction


class AgentSwitchRequested(Message):
    def __init__(self, delta: int) -> None:
        super().__init__()
        self.delta = delta


class PromptEditor(SafePasteTextArea):
    def __init__(self, *args: Any, placeholder: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if placeholder:
            with contextlib.suppress(Exception):
                setattr(self, "placeholder", placeholder)

    def action_copy(self) -> None:
        action = getattr(self.app, "action_interrupt_or_exit", None)
        if callable(action):
            action()
        else:
            super().action_copy()

    async def _on_key(self, event: events.Key) -> None:
        if event.key in {"ctrl+a", "super+a"}:
            event.stop()
            event.prevent_default()
            self.action_select_all()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(PromptSubmitted(self.text))
            return
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            self.action_copy()
            return
        if event.key in {"left", "right"}:
            event.stop()
            event.prevent_default()
            pointer_switch = getattr(
                self.app,
                "_pointer_requests_agent_switch",
                None,
            )
            if not self.text.strip() or (
                callable(pointer_switch) and pointer_switch()
            ):
                self.post_message(AgentSwitchRequested(-1 if event.key == "left" else 1))
            elif event.key == "left":
                self.action_cursor_left()
            else:
                self.action_cursor_right()
            return
        if event.key in {"up", "down"}:
            row = self.cursor_location[0]
            last_row = max(0, self.text.count("\n"))
            at_boundary = row == 0 if event.key == "up" else row >= last_row
            if at_boundary and self.selection.is_empty:
                event.stop()
                event.prevent_default()
                self.post_message(
                    PromptHistoryRequested(-1 if event.key == "up" else 1)
                )
                return
        if event.key in {"shift+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
            return
        if event.key in {"backspace", "ctrl+h"}:
            event.stop()
            event.prevent_default()
            self.action_delete_left()
            return
        if event.key in {"delete", "ctrl+d"}:
            event.stop()
            event.prevent_default()
            self.action_delete_right()
            return
        if event.key in {"ctrl+w", "ctrl+backspace", "alt+backspace"}:
            event.stop()
            event.prevent_default()
            self.action_delete_word_left()
            return
        if event.key == "alt+delete":
            event.stop()
            event.prevent_default()
            self.action_delete_word_right()
            return
        if event.key == "ctrl+u":
            event.stop()
            event.prevent_default()
            self.action_delete_to_start_of_line()
            return
        if event.key == "ctrl+k":
            event.stop()
            event.prevent_default()
            await self.action_delete_to_end_of_line_or_delete_line()
            return
        await super()._on_key(event)


class DetailScroll(VerticalScroll):
    """Track user scroll intent separately from the current animated offset."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.follow_tail = True

    def scroll_to(
        self,
        x: float | None = None,
        y: float | None = None,
        **kwargs: Any,
    ) -> None:
        if y is not None:
            self.follow_tail = y >= self.max_scroll_y
        super().scroll_to(x, y, **kwargs)

    def scroll_end(self, **kwargs: Any) -> None:
        self.follow_tail = True
        super().scroll_end(**kwargs)

    def follow_end_if_enabled(self, **kwargs: Any) -> None:
        if self.follow_tail:
            super().scroll_end(**kwargs)
            # Rich content can change the scroll extent one frame after the
            # Static update. Settle against that final geometry as well, while
            # still respecting a user scroll that happened in between.
            self.call_after_refresh(self._settle_follow_end, kwargs)

    def _settle_follow_end(self, kwargs: dict[str, Any]) -> None:
        if self.follow_tail:
            super().scroll_end(**kwargs)

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.follow_tail = False
        super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        super()._on_mouse_scroll_down(event)
        self.call_after_refresh(self._update_follow_tail)

    def _update_follow_tail(self) -> None:
        self.follow_tail = self.is_vertical_scroll_end

class DetailView(Static):
    """Rebuild cell-aware wrapping after the Detail viewport changes width."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._render_width = 0

    def on_resize(self, event: events.Resize) -> None:
        previous_width = self._render_width
        self._render_width = event.size.width
        if previous_width and previous_width != self._render_width:
            self.app.call_after_refresh(self.app._sync)


class FollowUpQueueView(Static):
    """Reflow hanging-indented queue rows when the viewport width changes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._render_width = 0

    def on_resize(self, event: events.Resize) -> None:
        previous_width = self._render_width
        self._render_width = event.size.width
        if previous_width and previous_width != self._render_width:
            self.app.call_after_refresh(self.app._refresh_follow_up_queue)


class RainbowDetailFrame(Vertical):
    """Update border colors without invalidating the Detail viewport."""

    BORDER_EDGES = ("top", "right", "bottom", "left")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rainbow_active = False

    def set_rainbow_colors(
        self,
        top: str,
        right: str,
        bottom: str,
        left: str,
        edge: str | None = None,
    ) -> None:
        colors = {
            "top": top,
            "right": right,
            "bottom": bottom,
            "left": left,
        }
        edges = self.BORDER_EDGES if edge is None else (edge,)
        if any(name not in colors for name in edges):
            raise ValueError(f"unknown border edge: {edge}")

        styles = self.styles
        dirty_edges: list[str] = []
        for name in edges:
            color = Color.parse(colors[name])
            changed = styles.set_rule(
                f"border_{name}",
                ("round", color),
            )
            if name == "top":
                changed = styles.set_rule("border_title_color", color) or changed
            if changed:
                dirty_edges.append(name)
        if not dirty_edges:
            return

        regions = tuple(
            region
            for name in dirty_edges
            if (region := self._border_content_region(name)) is not None
        )
        if regions:
            self.refresh(*regions)
        else:
            self.refresh()

    def _border_content_region(self, edge: str) -> Region | None:
        """Return one border strip in content coordinates for Widget.refresh."""
        width, height = self.outer_size
        if width <= 0 or height <= 0:
            return None
        offset_x, offset_y = self.content_offset
        if edge == "top":
            return Region(-offset_x, -offset_y, width, 1)
        if edge == "bottom":
            return Region(-offset_x, height - 1 - offset_y, width, 1)
        middle_height = max(0, height - 2)
        if not middle_height:
            return None
        if edge == "left":
            return Region(-offset_x, 1 - offset_y, 1, middle_height)
        if edge == "right":
            return Region(
                width - 1 - offset_x,
                1 - offset_y,
                1,
                middle_height,
            )
        raise ValueError(f"unknown border edge: {edge}")

