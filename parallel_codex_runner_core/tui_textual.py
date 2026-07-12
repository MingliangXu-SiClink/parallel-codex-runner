from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._vendor import activate_textual

TEXTUAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "show all TUI commands"),
    ("/status", "show current run configuration"),
    ("/config", "show current run configuration"),
    ("/kill [agent]", "stop a running agent while queued agents continue normally"),
    ("/numofagents <n>", "set the number of agents for the next run"),
    ("/maxparallel <n|auto>", "limit how many agents may run concurrently"),
    ("/serial", "run agents one at a time"),
    ("/parallel", "run agents concurrently"),
    (
        "/bestby <duration|reasoning_tokens>",
        "choose how the best successful agent is selected",
    ),
    ("/model <name|clear>", "set or clear the Codex model for the next run"),
    ("/workspace <path>", "set the workspace PCR operates on"),
    ("/runsdir <path|clear>", "set or reset the directory used for run data"),
    ("/codexbin <path>", "set the Codex executable"),
    ("/syncback <on|off>", "enable or disable syncing the selected result back"),
    ("/keepworkspaces <on|off>", "keep or clean up agent workspaces after a run"),
    ("/promptfile <path>", "read a prompt file and start a run"),
    ("/resumeinclude <on|off>", "include or exclude non-interactive resume sessions"),
    ("/resume", "show resumable Codex sessions"),
    ("/resume <n|session>", "load a session by list number or session ID"),
    ("/resume latest", "load latest session"),
    ("/resume clear", "start without resume"),
    ("/clear", "clear the current Detail view when safe"),
    ("/exit", "stop active agents, clean up, and quit"),
)
MAX_SUGGESTIONS = 9
TIP_ROTATION_SECONDS = 10.0
TIP_ICON_REFRESH_SECONDS = 0.1
TIP_ICON = "✦"
TIP_ICON_COLORS: tuple[str, ...] = (
    "#32404b",
    "#3e5260",
    "#4b6878",
    "#5a7f91",
    "#6b97aa",
    "#7db0c2",
    "#94c9d8",
    "#7db0c2",
    "#6b97aa",
    "#5a7f91",
    "#4b6878",
    "#3e5260",
)
TUI_TIPS: tuple[str, ...] = (
    "输入 / 可查看并补全命令。",
    "输入框为空时，←/→ 可切换 Agent。",
    "Shift-Enter 可在输入框中换行。",
    "选中Agent栏目文本即可复制。",
    "使用 /resume 可载入之前的 Codex 对话。",
    "后续提问会从当前显示的已完成 Agent 继续。",
    "某个 Agent 完成后，即可从该 Agent 继续提问。",
    "运行前可直接修改上方配置项。",
    "Ctrl-C 会依情境执行复制、清空输入或退出。",
    "KEEP_WORKSPACES 可保留候选工作目录。",
    "SYNC_BACK 控制是否同步选中的结果至工作区。",
    "注意本项目默认以Codex Full Access 权限运行。",
    "运行中输入 /kill，可终止当前显示且正在运行的 Agent，排队 Agent 会正常接替。",
)


def command_suggestions(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text.startswith("/"):
        return []
    return [
        f"{command}  {description}"
        for command, description in TEXTUAL_COMMANDS
        if command.startswith(text)
    ][:MAX_SUGGESTIONS]


def build_help_text() -> str:
    command_width = max(len(command) for command, _description in TEXTUAL_COMMANDS)
    lines = ["Commands:"]
    lines.extend(f"  {command:<{command_width}}  {description}" for command, description in TEXTUAL_COMMANDS)
    lines.extend(
        [
            "",
            "Enter normal text to start a parallel Codex run.",
            "Most option commands apply to the next run.",
            "If a completed run is pending, PCR finalizes the selected agent before changing config.",
            "Ctrl-C copies selected text; otherwise it clears a non-empty prompt or exits.",
        ]
    )
    return "\n".join(lines)


def compact_text(text: str, limit: int = 600) -> str:
    return text.strip()


def compact_block(text: str, limit: int = 2400) -> str:
    return text.strip()


def format_seconds(value: Any) -> str:
    if value is None:
        return ""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if seconds < 0 or seconds != seconds:
        return ""
    return f"{seconds:.2f}s"


def value_at(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("message", "text", "content", "delta", "summary", "reasoning", "item"):
            text = text_value(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := text_value(item)))
    return ""


def command_completion_line(status: str, exit_code: Any) -> str:
    if exit_code is not None:
        try:
            succeeded = int(exit_code) == 0
        except (TypeError, ValueError):
            succeeded = str(exit_code).strip() == "0"
        return f"{'✓' if succeeded else '✗'} exit {exit_code}"
    normalized = status.strip().casefold().replace("-", "_")
    if normalized in {"completed", "complete", "success", "succeeded"}:
        return "✓ done"
    if normalized in {"failed", "failure", "error"}:
        return "✗ failed"
    if normalized in {"cancelled", "canceled"}:
        return "× cancelled"
    if normalized and normalized not in {"in_progress", "running", "started"}:
        return f"· {status.strip()}"
    return ""


def is_detail_noise_line(text: str) -> bool:
    normalized = " ".join(text.casefold().replace("_", " ").replace("-", " ").split())
    timestamp_stripped = re.sub(
        r"^\d{4}[- ]\d{2}[- ]\d{2}(?:[ t]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?\s*",
        "",
        text.casefold(),
    )
    without_timestamp = " ".join(timestamp_stripped.replace("_", " ").replace("-", " ").split())
    return (
        re.search(r"\bcodex\s+models\s+manager\b", normalized) is not None
        or "apply patch verification failed" in normalized
        or "failed to find expected lines" in normalized
        or without_timestamp in {"run result", "run result:"}
        or re.match(r"^best agent\s*:", without_timestamp) is not None
        or normalized.startswith(("synced", "run root"))
    )


def strip_detail_event_prefix(text: str) -> str:
    lowered = text.casefold()
    for prefix in ("agent_reasoning", "agent_message"):
        if lowered.startswith(f"{prefix}:"):
            return text.split(":", 1)[1].strip()
    return text


def detail_display_text(text: str) -> str:
    stripped = strip_detail_event_prefix(text.strip())
    if not stripped or is_detail_noise_line(stripped):
        return ""
    return compact_text(stripped)


def line_category_from_json(payload: dict[str, Any]) -> str:
    signals = [
        payload.get("type"),
        payload.get("event"),
        value_at(payload, "payload", "type"),
        value_at(payload, "item", "type"),
        value_at(payload, "payload", "item", "type"),
        value_at(payload, "role"),
        value_at(payload, "payload", "role"),
        value_at(payload, "item", "role"),
        value_at(payload, "payload", "item", "role"),
    ]
    signal = " ".join(str(value) for value in signals if value).casefold()
    if "reasoning" in signal or "thinking" in signal:
        return "thought"
    if "agent_message" in signal or "assistant" in signal:
        return "output"
    if " message" in f" {signal} " and "user" not in signal:
        return "output"
    return "activity"


def display_line_from_json(payload: dict[str, Any]) -> str:
    return display_line_parts_from_json(payload)[1]


def display_line_parts_from_json(payload: dict[str, Any]) -> tuple[str, str]:
    item = payload.get("item")
    if not isinstance(item, dict):
        item = value_at(payload, "payload", "item")
    if isinstance(item, dict) and item.get("type") == "command_execution":
        command = text_value(item.get("command"))
        status = text_value(item.get("status"))
        exit_code = item.get("exit_code")
        header = f"$ {command}" if command else "$ command"
        completion = command_completion_line(status, exit_code)
        return "activity", f"{header}\n{completion}" if completion else header

    text_paths = [
        ("message",),
        ("text",),
        ("content",),
        ("delta",),
        ("summary",),
        ("reasoning",),
        ("item", "message"),
        ("item", "text"),
        ("item", "content"),
        ("item", "delta"),
        ("item", "summary"),
        ("item", "reasoning"),
        ("payload", "message"),
        ("payload", "text"),
        ("payload", "content"),
        ("payload", "delta"),
        ("payload", "summary"),
        ("payload", "reasoning"),
        ("payload", "item", "message"),
        ("payload", "item", "text"),
        ("payload", "item", "content"),
        ("payload", "item", "delta"),
        ("payload", "item", "summary"),
        ("payload", "item", "reasoning"),
    ]
    for path in text_paths:
        text = text_value(value_at(payload, *path))
        if text:
            return line_category_from_json(payload), detail_display_text(text)
    return "activity", ""


def display_line_from_output(text: str) -> str:
    return display_line_parts_from_output(text)[1]


def display_line_parts_from_output(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped or is_detail_noise_line(stripped):
        return "activity", ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        lowered = stripped.casefold()
        category = "thought" if lowered.startswith("agent_reasoning:") else "output" if lowered.startswith("agent_message:") else "activity"
        return category, detail_display_text(stripped)
    if isinstance(payload, dict):
        return display_line_parts_from_json(payload)
    return "activity", ""


def same_display_message(left: str, right: str) -> bool:
    a = " ".join(left.split())
    b = " ".join(right.split())
    if not a or not b:
        return False
    if a == b:
        return True
    if a.endswith("..."):
        return b.startswith(a[:-3].rstrip())
    if b.endswith("..."):
        return a.startswith(b[:-3].rstrip())
    return False


try:
    activate_textual()
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.content import Content
    from textual.containers import Grid, Vertical, VerticalScroll
    from textual.message import Message
    from textual.widgets import Input, Select, Static, TextArea
except (ModuleNotFoundError, RuntimeError) as exc:
    _TEXTUAL_IMPORT_ERROR = exc

    def run_textual_tui(
        _args: argparse.Namespace, _exc: Exception = _TEXTUAL_IMPORT_ERROR
    ) -> int:
        raise SystemExit(
            "交互式 TUI 无法加载项目内置 Textual：请运行 python3 -m pip install -e . 重新安装。"
        ) from _exc

else:
    from .app import (
        get_codex_home,
        infer_codex_thread_id_for_result,
        list_resume_sessions,
        load_codex_session_history,
        normalize_best_by,
        promote_best_codex_session_to_workspace,
        run_once,
        subagent_resume_error,
    )
    from .models import AgentResult, CodexHistoryEntry
    from .paths import absolute_path_for_display, choose_run_base, default_run_anchor, is_relative_to
    from .workspace import cleanup_workspace_copies, sync_best_workspace_back
    from rich.cells import cell_len
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    def fold_text_by_cells(text: str, width: int) -> str:
        if width <= 1:
            return text
        folded_lines: list[str] = []
        source_lines = text.splitlines() or [""]
        for source in source_lines:
            current = ""
            current_width = 0
            for char in source:
                char_width = max(1, cell_len(char))
                if current and current_width + char_width > width:
                    folded_lines.append(current)
                    current = char
                    current_width = char_width
                else:
                    current += char
                    current_width += char_width
            folded_lines.append(current)
        return "\n".join(folded_lines)

    HELP_TEXT = build_help_text()


    def codex_model_options(current_model: str | None) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = [("default", "")]
        seen = {""}
        cache_path = get_codex_home() / "models_cache.json"
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        models = payload.get("models") if isinstance(payload, dict) else None
        if isinstance(models, list):
            for model in models:
                if not isinstance(model, dict) or model.get("visibility") == "hide":
                    continue
                slug = str(model.get("slug") or "").strip()
                if slug and slug not in seen:
                    options.append((slug, slug))
                    seen.add(slug)
        current = str(current_model or "").strip()
        if current and current not in seen:
            options.append((current, current))
        return options


    def resume_select_options(
        sessions: list[Any],
        current_session_id: str,
    ) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = [("NO", "")]
        seen = {""}
        for session in sessions:
            session_id = str(getattr(session, "session_id", "") or "").strip()
            if not session_id or session_id in seen:
                continue
            title = " ".join(str(getattr(session, "title", "") or "").split())
            label = f"{title}  [{session_id}]" if title else session_id
            options.append((label, session_id))
            seen.add(session_id)
        if current_session_id and current_session_id not in seen:
            options.append((current_session_id, current_session_id))
        return options


    @dataclass
    class AgentPane:
        idx: int
        status: str = "idle"
        reasoning_tokens: int | None = None
        input_text: str = ""
        final_text: str = ""
        result: dict[str, Any] | None = None
        lines: list[str] = field(default_factory=list)
        thought_lines: list[str] = field(default_factory=list)
        output_lines: list[str] = field(default_factory=list)
        revision: int = 0

        def append(self, text: str, category: str = "activity") -> None:
            text = text.strip()
            if not text:
                return
            bucket = self.thought_lines if category == "thought" else self.output_lines if category == "output" else self.lines
            if bucket is self.lines and "\n" in text:
                for index in range(len(bucket) - 1, -1, -1):
                    if text.startswith(f"{bucket[index]}\n"):
                        bucket[index] = text
                        return
            bucket.append(text)

        def clear_detail(self) -> None:
            self.lines.clear()
            self.thought_lines.clear()
            self.output_lines.clear()


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


    class PromptSubmitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value


    class AgentSwitchRequested(Message):
        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta


    class PromptEditor(TextArea):
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
                if not self.text.strip():
                    self.post_message(AgentSwitchRequested(-1 if event.key == "left" else 1))
                elif event.key == "left":
                    self.action_cursor_left()
                else:
                    self.action_cursor_right()
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
            if event.key in {"ctrl+u", "super+backspace"}:
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

        def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
            self.follow_tail = False
            super()._on_mouse_scroll_up(event)

        def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
            super()._on_mouse_scroll_down(event)
            self.call_after_refresh(self._update_follow_tail)

        def _update_follow_tail(self) -> None:
            self.follow_tail = self.is_vertical_scroll_end

    class PcrTextualApp(App[None]):
        CSS = """
        Screen {
            background: #101216;
            color: #e7ebf2;
        }
        #root {
            height: 100%;
        }
        .caption {
            height: 1;
            margin: 0 1;
            padding: 0 1;
            color: #8fb3ff;
            text-style: bold;
        }
        #runner-frame {
            height: auto;
            margin: 0 1;
            padding: 0 1;
            border: round cyan;
            border-title-style: bold;
            overflow: hidden;
        }
        #runner-grid {
            height: auto;
            grid-size: 2;
            grid-columns: 18 1fr;
            grid-rows: auto;
            grid-gutter: 0 1;
        }
        .runner-key {
            height: auto;
            min-height: 1;
            color: #e7ebf2;
            text-style: bold;
        }
        .runner-value {
            height: 1;
            min-height: 1;
            max-height: 1;
            text-wrap: nowrap;
            text-overflow: clip;
        }
        .runner-control {
            height: 1;
            min-height: 1;
            border: none;
            padding: 0;
            background: #171d25;
        }
        .runner-control:focus {
            background: #243448;
            color: #ffffff;
        }
        #config-agents, #config-max-parallel {
            width: 12;
        }
        #config-execution, #config-best-by {
            width: 24;
        }
        #config-model {
            width: 36;
        }
        #config-sync-back, #config-keep-workspaces {
            width: 10;
        }
        #detail-frame {
            height: 1fr;
            margin: 0 1;
            border: round cyan;
        }
        #detail-scroll {
            height: 1fr;
        }
        #detail {
            width: 100%;
            padding: 0 1;
        }
        #suggestions {
            height: 0;
            min-height: 0;
            max-height: 24;
            margin: 0 1;
            padding: 0 1;
            color: #b7c8e6;
            background: #101216;
        }
        #tips {
            height: 1;
            min-height: 1;
            max-height: 1;
            margin: 0 1;
            padding: 0 1;
            color: #aebed3;
            background: #171d25;
            content-align: left middle;
            text-wrap: nowrap;
            text-overflow: clip;
        }
        #prompt {
            height: 3;
            min-height: 3;
            max-height: 20;
            margin: 0 1;
            padding: 0 1;
            border: round #4c5f74;
            background: #0d1117;
        }
        #state {
            height: 1;
            margin: 0 1;
            padding: 0 1;
            content-align: left middle;
            background: #171d25;
            color: #ffffff;
            text-style: bold;
        }
        """

        BINDINGS = [
            ("ctrl+c", "interrupt_or_exit", "Copy/Clear/Exit"),
            ("super+c", "copy_selection", "Copy"),
            ("ctrl+l", "clear_view", "Clear"),
        ]

        def __init__(self, args: argparse.Namespace) -> None:
            super().__init__()
            self.args = args
            self.args.resume_include_non_interactive = True
            self.workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
            self.num_agents = args.num_agents
            self.resume_session_id = (args.resume_session_id or "").strip()
            self.resume_entries = []
            self.model_choices = codex_model_options(getattr(args, "model", None))
            self.resume_choices = resume_select_options([], self.resume_session_id)
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.selected_agent = 1
            self.running = False
            self.status = "Ready"
            self.best_agent: int | None = None
            self.started_at: float | None = None
            self.prompt_height = 3
            self.suggestion_line_count = 0
            self.tip_index = 0
            self.tip_icon_index = 0
            self.work_frame = 0
            self.exit_after_run = False
            self.cancel_event: threading.Event | None = None
            self.agent_cancel_events: dict[int, threading.Event] = {}
            self.runner_thread: threading.Thread | None = None
            self._shutdown_cleanup_started = False
            self.run_info_rows = self._base_info_rows()
            self.pending_run_root: Path | None = None
            self.pending_workspaces_root: Path | None = None
            self.detail_history: list[tuple[str, str, str]] = []
            self.command_history: list[tuple[str, str, str]] = []
            self.detail_revision = 0
            self.resume_history_request = 0
            self.resume_choices_request = 0
            self.resume_choices_loaded = False
            self.resume_choices_inflight: tuple[int, Path] | None = None
            self.pending_resume_selector: str | None = None
            self._resume_io_lock = threading.Lock()
            self.queued_prompt = ""
            self.queued_agent: int | None = None
            self._updating_controls = False
            self._committed_input_values = {
                "config-agents": str(self.num_agents),
                "config-max-parallel": dict(self.run_info_rows).get("MAX_PARALLEL", ""),
            }
            self._mouse_down_in_runner_control = False
            self._last_screen_selection = ""
            self._sync_deferred_for_selection = False
            self._tip_refresh_deferred_for_selection = False
            self._detail_cache_key: tuple[Any, ...] | None = None
            self._detail_cache_renderable: object = ""

        def _is_runner_control(self, node: Any) -> bool:
            while node is not None:
                if isinstance(node, (Input, Select)) and node.has_class("runner-control"):
                    return True
                node = node.parent
            return False

        def _is_detail_widget(self, node: Any) -> bool:
            while node is not None:
                if getattr(node, "id", None) in {"detail", "detail-scroll"}:
                    return True
                node = node.parent
            return False

        @staticmethod
        def _is_node_within(node: Any, ancestor: Any) -> bool:
            while node is not None:
                if node is ancestor:
                    return True
                node = node.parent
            return False

        def _release_stale_mouse_capture(self, clicked_widget: Any) -> None:
            captured = self.mouse_captured
            if captured is not None and not self._is_node_within(clicked_widget, captured):
                # A fresh press outside the captured widget means its previous
                # mouse-up was lost (for example, outside the terminal window).
                # Leaving capture active prevents Screen from starting any
                # subsequent text selection.
                self.capture_mouse(None)

        def _selection_drag_active(self) -> bool:
            try:
                return bool(self.screen._selecting)
            except Exception:
                return False

        def _clear_screen_selection_for_interaction(self) -> None:
            try:
                if self.screen.selections:
                    self.screen.clear_selection()
            except Exception:
                return
            self._last_screen_selection = ""
            self._flush_selection_deferred_updates()

        def _flush_selection_deferred_updates(self) -> None:
            if self._selection_drag_active():
                return
            refresh_tip = self._tip_refresh_deferred_for_selection
            sync = self._sync_deferred_for_selection
            self._tip_refresh_deferred_for_selection = False
            self._sync_deferred_for_selection = False
            if refresh_tip:
                self._refresh_tip()
            if sync:
                self._sync()

        async def on_event(self, event: events.Event) -> None:
            if isinstance(event, events.AppBlur) and self.mouse_captured is not None:
                self.capture_mouse(None)

            if isinstance(event, events.MouseDown) and not event.is_forwarded:
                try:
                    clicked_widget, _offset = self.screen.get_widget_and_offset_at(
                        event.x,
                        event.y,
                    )
                except Exception:
                    self._mouse_down_in_runner_control = False
                else:
                    self._release_stale_mouse_capture(clicked_widget)
                    self._mouse_down_in_runner_control = self._is_runner_control(clicked_widget)
                    if not self._is_detail_widget(clicked_widget):
                        self._last_screen_selection = ""

            if (
                isinstance(event, events.Key)
                and not event.is_forwarded
                and event.key not in {"ctrl+c", "super+c"}
            ):
                self._clear_screen_selection_for_interaction()

            is_text_input = (
                isinstance(event, events.Key) and event.is_printable
            ) or isinstance(event, events.Paste)
            if (
                is_text_input
                and not event.is_forwarded
                and not self._is_runner_control(self.focused)
            ):
                self._last_screen_selection = ""
                try:
                    prompt = self.query_one("#prompt", PromptEditor)
                except Exception:
                    pass
                else:
                    if self.focused is not prompt:
                        prompt.focus()
                        insert = event.character if isinstance(event, events.Key) else event.text
                        if insert:
                            result = prompt.replace(
                                insert,
                                *prompt.selection,
                                maintain_selection_offset=False,
                            )
                            prompt.move_cursor(result.end_location)
                        event.stop()
                        event.prevent_default()
                        return
            await super().on_event(event)

        @on(events.TextSelected)
        def _on_screen_text_selected(self, _event: events.TextSelected) -> None:
            self._complete_pointer_selection()

        @on(events.Click)
        def _on_screen_multi_click(self, event: events.Click) -> None:
            if event.chain >= 2:
                # Double/triple click selection is applied by Widget._on_click,
                # after Screen has already emitted TextSelected for mouse-up.
                self.call_after_refresh(self._complete_pointer_selection)

        def _complete_pointer_selection(self) -> None:
            selected = self.screen.get_selected_text() or ""
            if selected:
                if selected != self._last_screen_selection:
                    self.copy_to_clipboard(selected)
                self._last_screen_selection = selected
                self._flush_selection_deferred_updates()
                return
            self._last_screen_selection = ""
            if not self._mouse_down_in_runner_control:
                self.query_one("#prompt", PromptEditor).focus()
            self._flush_selection_deferred_updates()

        def compose(self) -> ComposeResult:
            with Vertical(id="root"):
                with Vertical(id="runner-frame"):
                    with Grid(id="runner-grid"):
                        yield Static("CONVERSATION", classes="runner-key")
                        yield Static("", id="runner-conversation", classes="runner-value", markup=False)
                        yield Static("WORKSPACE", classes="runner-key")
                        yield Static("", id="runner-workspace", classes="runner-value", markup=False)
                        yield Static("RUNS_ROOT", classes="runner-key")
                        yield Static("", id="runner-runs-root", classes="runner-value", markup=False)
                        yield Static("AGENTS", classes="runner-key")
                        yield Input(
                            str(self.num_agents),
                            id="config-agents",
                            classes="runner-control",
                            type="integer",
                        )
                        yield Static("EXECUTION", classes="runner-key")
                        yield Select(
                            [("parallel", "parallel"), ("serial", "serial")],
                            value="serial" if getattr(self.args, "serial", False) else "parallel",
                            allow_blank=False,
                            compact=True,
                            id="config-execution",
                            classes="runner-control",
                        )
                        yield Static("MAX_PARALLEL", classes="runner-key")
                        yield Input(
                            str(dict(self._base_info_rows())["MAX_PARALLEL"]),
                            id="config-max-parallel",
                            classes="runner-control",
                        )
                        yield Static("BEST_BY", classes="runner-key")
                        yield Select(
                            [("reasoning_tokens", "reasoning_tokens"), ("duration", "duration")],
                            value=str(getattr(self.args, "best_by", "reasoning_tokens")),
                            allow_blank=False,
                            compact=True,
                            id="config-best-by",
                            classes="runner-control",
                        )
                        yield Static("MODEL", classes="runner-key")
                        yield Select(
                            self.model_choices,
                            value=str(getattr(self.args, "model", None) or ""),
                            allow_blank=False,
                            compact=True,
                            id="config-model",
                            classes="runner-control",
                        )
                        yield Static("CODEX_BIN", classes="runner-key")
                        yield Static("", id="runner-codex-bin", classes="runner-value", markup=False)
                        yield Static("SYNC_BACK", classes="runner-key")
                        yield Select(
                            [("YES", True), ("NO", False)],
                            value=not bool(getattr(self.args, "no_sync_back", False)),
                            allow_blank=False,
                            compact=True,
                            id="config-sync-back",
                            classes="runner-control",
                        )
                        yield Static("KEEP_WORKSPACES", classes="runner-key")
                        yield Select(
                            [("YES", True), ("NO", False)],
                            value=bool(getattr(self.args, "keep_workspaces", False)),
                            allow_blank=False,
                            compact=True,
                            id="config-keep-workspaces",
                            classes="runner-control",
                        )
                        yield Static("RESUME", classes="runner-key")
                        yield Select(
                            self.resume_choices,
                            value=self.resume_session_id,
                            allow_blank=False,
                            compact=True,
                            id="config-resume",
                            classes="runner-control",
                        )
                        yield Static("BEST AGENT", id="runner-best-agent-key", classes="runner-key")
                        yield Static("", id="runner-best-agent", classes="runner-value", markup=False)
                with Vertical(id="detail-frame"):
                    with DetailScroll(id="detail-scroll"):
                        yield Static("", id="detail")
                yield Static("", id="suggestions")
                yield Static(self._tip_renderable(), id="tips")
                yield PromptEditor(
                    "",
                    id="prompt",
                    soft_wrap=True,
                    show_line_numbers=False,
                    placeholder="输入需求，或输入 / 查看命令",
                )
                yield Static("", id="state")

        def on_mount(self) -> None:
            self.query_one("#runner-frame", Vertical).border_title = "PARALLEL-CODEX-RUNNER"
            self.set_interval(0.25, self._tick)
            self.set_interval(TIP_ROTATION_SECONDS, self._advance_tip)
            self.set_interval(TIP_ICON_REFRESH_SECONDS, self._advance_tip_icon)
            if (
                not self.is_headless
                and not getattr(self.args, "resume", False)
                and not self.resume_session_id
            ):
                self._refresh_resume_control()
            if getattr(self.args, "resume", False) and not self.resume_session_id:
                self._handle_resume([])
            elif self.resume_session_id:
                self._select_resume_session(self.resume_session_id)
            else:
                self._sync()
            self.query_one("#prompt", PromptEditor).focus()

        @on(PromptSubmitted)
        def _on_prompt(self, event: PromptSubmitted) -> None:
            prompt = self.query_one("#prompt", PromptEditor)
            value = event.value.strip()
            if not value:
                return
            if value.startswith("/"):
                prompt.clear()
                self._update_suggestions("")
                self._sync_prompt_height()
                self._handle_command(value)
            else:
                if self._start_run(value):
                    prompt.clear()
                    self._update_suggestions("")
                    self._sync_prompt_height()
            prompt.focus()

        @on(TextArea.Changed)
        def _on_text_changed(self, event: TextArea.Changed) -> None:
            if event.text_area.id == "prompt":
                self._update_suggestions(event.text_area.text)
                self._sync_prompt_height()

        @on(AgentSwitchRequested)
        def _on_switch(self, event: AgentSwitchRequested) -> None:
            self._switch_agent(event.delta)

        def _set_committed_input_value(self, control: Input, value: str) -> None:
            self._updating_controls = True
            try:
                control.value = value
                if control.id is not None:
                    self._committed_input_values[control.id] = value
            finally:
                self._updating_controls = False

        def _commit_agents_control(self) -> bool:
            try:
                control = self.query_one("#config-agents", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get("config-agents"):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_numofagents([value_text])
            applied = requested is not None and requested > 0 and self.num_agents == requested
            self._set_committed_input_value(control, str(self.num_agents))
            return applied

        def _commit_max_parallel_control(self) -> bool:
            try:
                control = self.query_one("#config-max-parallel", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get("config-max-parallel"):
                return True
            normalized = value_text.lower()
            if normalized in {"auto", "default", "clear", "none"}:
                requested: int | None = None
                valid = True
            else:
                try:
                    requested = int(normalized)
                except ValueError:
                    requested = None
                    valid = False
                else:
                    valid = requested > 0
            self._handle_maxparallel([value_text])
            applied = valid and getattr(self.args, "max_parallel", None) == requested
            display_value = dict(self._base_info_rows()).get("MAX_PARALLEL", "")
            self._set_committed_input_value(control, display_value)
            return applied

        def _commit_runner_inputs(self) -> bool:
            if not self._commit_agents_control():
                self.query_one("#config-agents", Input).focus()
                return False
            if not self._commit_max_parallel_control():
                self.query_one("#config-max-parallel", Input).focus()
                return False
            return True

        @on(Input.Submitted, "#config-agents")
        def _on_agents_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_agents_control()

        @on(events.DescendantBlur, "#config-agents")
        def _on_agents_blurred(self, _event: events.DescendantBlur) -> None:
            if not self._updating_controls:
                self._commit_agents_control()

        @on(Input.Submitted, "#config-max-parallel")
        def _on_max_parallel_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_max_parallel_control()

        @on(events.DescendantBlur, "#config-max-parallel")
        def _on_max_parallel_blurred(self, _event: events.DescendantBlur) -> None:
            if not self._updating_controls:
                self._commit_max_parallel_control()

        @on(Select.Changed, "#config-execution")
        def _on_execution_selected(self, event: Select.Changed) -> None:
            if self._updating_controls:
                return
            serial = str(event.value) == "serial"
            current_serial = dict(self._tree_rows()).get("EXECUTION") == "serial"
            if serial != current_serial:
                self._handle_execution(serial=serial)

        @on(Select.Changed, "#config-best-by")
        def _on_best_by_selected(self, event: Select.Changed) -> None:
            if self._updating_controls:
                return
            value = str(event.value)
            if value != str(getattr(self.args, "best_by", "reasoning_tokens")):
                self._handle_bestby([value])

        @on(Select.Changed, "#config-model")
        def _on_model_selected(self, event: Select.Changed) -> None:
            if self._updating_controls:
                return
            value = str(event.value)
            if value != str(getattr(self.args, "model", None) or ""):
                self._handle_model([value or "clear"])

        @on(Select.Changed, "#config-sync-back")
        def _on_sync_back_toggled(self, event: Select.Changed) -> None:
            if self._updating_controls:
                return
            current = not bool(getattr(self.args, "no_sync_back", False))
            value = bool(event.value)
            if value != current:
                self._handle_syncback(["on" if value else "off"])

        @on(Select.Changed, "#config-keep-workspaces")
        def _on_keep_workspaces_toggled(self, event: Select.Changed) -> None:
            if self._updating_controls:
                return
            current = bool(getattr(self.args, "keep_workspaces", False))
            value = bool(event.value)
            if value != current:
                self._handle_keepworkspaces(["on" if value else "off"])

        @on(Select.Changed, "#config-resume")
        def _on_resume_selected(self, event: Select.Changed) -> None:
            if self._updating_controls:
                return
            session_id = str(event.value)
            if session_id == self.resume_session_id:
                return
            self._handle_resume([session_id] if session_id else ["clear"])

        async def _on_key(self, event: events.Key) -> None:
            if event.key in {"left", "right"}:
                prompt = self.query_one("#prompt", PromptEditor)
                if (
                    self.focused is not prompt
                    and not isinstance(self.focused, (Input, Select))
                    and not prompt.text.strip()
                ):
                    event.stop()
                    event.prevent_default()
                    self._switch_agent(-1 if event.key == "left" else 1)
                    prompt.focus()
                    return
            await super()._on_key(event)

        def _switch_agent(self, delta: int) -> None:
            self.selected_agent = min(self.num_agents, max(1, self.selected_agent + delta))
            self._sync()

        def _agent_kill_requested(self, idx: int) -> bool:
            event = self.agent_cancel_events.get(idx)
            return bool(event is not None and event.is_set())

        @on(RunnerEvent)
        def _on_runner_event(self, event: RunnerEvent) -> None:
            payload = event.payload
            idx = int(payload.get("idx") or 0)
            pane = self.agents.get(idx)
            kind = str(payload.get("type") or "")
            if kind == "run_prepared":
                rows = payload.get("rows")
                if isinstance(rows, list):
                    run_rows = [(str(k), str(v)) for k, v in rows if isinstance(k, str)]
                    self._remember_run_paths(run_rows)
                    self.run_info_rows = self._visible_info_rows(run_rows)
            elif kind == "agent_status" and pane is not None:
                pane.status = (
                    "stopping"
                    if self._agent_kill_requested(idx)
                    else str(payload.get("status") or pane.status)
                )
                self._mark_detail_dirty(pane)
            elif kind == "agent_started" and pane is not None:
                pane.status = "stopping" if self._agent_kill_requested(idx) else "running"
                self._mark_detail_dirty(pane)
            elif kind == "agent_tokens" and pane is not None:
                value = payload.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
            elif kind == "agent_line" and pane is not None:
                if not self._agent_kill_requested(idx):
                    pane.status = "running"
                category, text = display_line_parts_from_output(str(payload.get("text") or ""))
                pane.append(text, category)
                self._mark_detail_dirty(pane)
            elif kind == "agent_finished" and pane is not None:
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                pane.result = result
                pane.status = str(result.get("status") or "finished")
                value = result.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
                final_text = self._read_final_message(result) if pane.status == "success" else ""
                if final_text:
                    pane.final_text = final_text
                self._mark_detail_dirty(pane)
            elif kind == "run_finished":
                self.running = False
                self.cancel_event = None
                run_root = payload.get("run_root")
                if isinstance(run_root, str) and run_root:
                    self.pending_run_root = Path(run_root)
                    self.pending_workspaces_root = self.pending_run_root / "workspaces"
                self.best_agent = payload.get("best_agent") if isinstance(payload.get("best_agent"), int) else None
                if payload.get("cancelled"):
                    if self.queued_prompt and self.queued_agent is not None:
                        self._continue_queued_prompt()
                    else:
                        self.status = "Cancelled"
                        if self._cleanup_after_pending_run():
                            self._clear_pending_run()
                else:
                    self.status = f"Done: agent_{self.best_agent:03d}" if self.best_agent else "No successful agent"
            elif kind == "run_failed":
                self.running = False
                self.cancel_event = None
                if self.queued_prompt and self.queued_agent is not None:
                    self._continue_queued_prompt()
                else:
                    self.status = f"Run failed: {payload.get('message') or ''}"
                    if self._cleanup_after_pending_run():
                        self._clear_pending_run()
            if kind not in {"agent_line", "agent_tokens", "agent_status"}:
                self._sync()
            if kind in {"run_finished", "run_failed"} and self.exit_after_run and not self._has_pending_run():
                self.exit()

        @on(ResumeHistoryLoaded)
        def _on_resume_history_loaded(self, event: ResumeHistoryLoaded) -> None:
            if event.request_id != self.resume_history_request:
                return
            if event.rejected:
                self.status = event.error or f"Cannot resume session: {event.session_id}"
                self._sync()
                return

            self.resume_session_id = event.session_id
            self._reset_conversation_detail()
            self.run_info_rows = self._base_info_rows()
            if event.error:
                loaded_status = f"Resume history unavailable: {event.error}"
            else:
                self.detail_history = [self._history_detail_block(entry) for entry in event.entries]
                loaded_status = (
                    f"Resume session loaded: {event.session_id}"
                    if event.entries
                    else f"Resume session loaded without readable history: {event.session_id}"
                )
            if not self.running and not self._has_pending_run():
                self.status = loaded_status
            self._mark_detail_dirty()
            self._sync()

        @on(ResumeChoicesLoaded)
        def _on_resume_choices_loaded(self, event: ResumeChoicesLoaded) -> None:
            if self.resume_choices_inflight == (event.request_id, event.workspace):
                self.resume_choices_inflight = None
            if event.request_id != self.resume_choices_request or event.workspace != self.workspace:
                return
            if event.error:
                selector = self.pending_resume_selector
                self.pending_resume_selector = None
                self.resume_choices_loaded = False
                if selector is not None:
                    self.status = f"Cannot load resume sessions: {event.error}"
                    self._sync()
                return
            self.resume_choices_loaded = True
            self._apply_resume_choices(event.entries)
            selector = self.pending_resume_selector
            self.pending_resume_selector = None
            if selector is not None:
                self._handle_loaded_resume_selector(selector)

        def action_clear_view(self) -> None:
            if self.running:
                self.status = "Cannot clear while a run is active"
                self._sync()
                return
            if self._has_pending_run():
                if self.best_agent is None:
                    if not self._discard_pending_run():
                        self._sync()
                        return
                else:
                    self.command_history.clear()
                    self._mark_detail_dirty()
                    self.status = "Cleared command view"
                    self._sync()
                    return
            for pane in self.agents.values():
                pane.status = "idle"
                pane.reasoning_tokens = None
                pane.input_text = ""
                pane.final_text = ""
                pane.result = None
                pane.clear_detail()
            self.best_agent = None
            self.resume_history_request += 1
            self.detail_history.clear()
            self.command_history.clear()
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = "Ready"
            self._sync()

        def copy_to_clipboard(self, text: str) -> None:
            if sys.platform == "darwin" and shutil.which("pbcopy") is not None:
                # Textual's fallback emits the entire payload as OSC 52. Large,
                # accumulated Detail histories can saturate the terminal writer
                # and make the UI appear to stop accepting mouse selection.
                self._clipboard = text
                try:
                    subprocess.run(
                        ["pbcopy"],
                        input=text,
                        text=True,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                    return
                except (OSError, subprocess.SubprocessError):
                    return
            super().copy_to_clipboard(text)

        def _copy_active_text(self) -> bool:
            if self._last_screen_selection:
                selected = self._last_screen_selection
                self._last_screen_selection = ""
                self.copy_to_clipboard(selected)
                return True
            screen_selection = self.screen.get_selected_text()
            if screen_selection:
                self._last_screen_selection = screen_selection
                self.copy_to_clipboard(screen_selection)
                return True
            selected_text = getattr(self.focused, "selected_text", "")
            if isinstance(selected_text, str) and selected_text:
                self.copy_to_clipboard(selected_text)
                return True
            if isinstance(self.focused, Input):
                if self.focused.value:
                    self.copy_to_clipboard(self.focused.value)
                    return True
            if isinstance(self.focused, Select):
                value = self.focused.value
                if isinstance(value, bool):
                    text = "YES" if value else "NO"
                elif value == "":
                    text = "default" if self.focused.id == "config-model" else "NO"
                else:
                    text = str(value)
                self.copy_to_clipboard(text)
                return True
            return False

        def action_copy_selection(self) -> None:
            self._copy_active_text()

        async def action_quit(self) -> None:
            # Textual's priority Ctrl-Q binding calls this action. Keep it on
            # PCR's cancellation/finalization path instead of exiting directly.
            self._request_exit()

        def action_interrupt_or_exit(self) -> None:
            if self._copy_active_text():
                return
            prompt = self.query_one("#prompt", PromptEditor)
            if prompt.text.strip():
                prompt.clear()
                self._update_suggestions("")
                self._sync_prompt_height()
                prompt.focus()
            else:
                self._request_exit()

        def _request_exit(self) -> None:
            if self.running:
                self.queued_prompt = ""
                self.queued_agent = None
                self.exit_after_run = True
                if self.cancel_event is not None:
                    self.cancel_event.set()
                self.status = "Stopping and cleaning up"
                self._sync()
                return
            if self._has_pending_run():
                if getattr(self.args, "no_sync_back", False):
                    if not self._cleanup_after_pending_run():
                        self._sync()
                        return
                    self._clear_pending_run()
                    self.exit()
                    return
                if self.best_agent is not None and not self._finalize_agent(self.best_agent, require_resume=False):
                    self._sync()
                    return
                if self.best_agent is None:
                    if not self._cleanup_after_pending_run():
                        self._sync()
                        return
                    self._clear_pending_run()
            self.exit()

        def _handle_command(self, raw: str) -> None:
            try:
                parts = shlex.split(raw)
            except ValueError as exc:
                self.status = f"Command parse error: {exc}"
                self._sync()
                return
            if not parts:
                return
            name = parts[0].lower()
            args = parts[1:]
            if name in {"/exit", "/quit"}:
                self._request_exit()
                return
            if name == "/help":
                self._show_text(HELP_TEXT)
                return
            if name in {"/status", "/config"}:
                self._show_status()
                return
            if name == "/clear":
                self.action_clear_view()
                return
            if name in {"/kill", "/killagent", "/kill-agent"}:
                self._handle_kill(args)
                return
            if name in {"/numofagents", "/agents"}:
                self._handle_numofagents(args)
                return
            if name in {"/maxparallel", "/max-parallel"}:
                self._handle_maxparallel(args)
                return
            if name == "/serial":
                self._handle_execution(serial=True)
                return
            if name == "/parallel":
                self._handle_execution(serial=False)
                return
            if name in {"/bestby", "/best-by", "/candidateby", "/candidate-by"}:
                self._handle_bestby(args)
                return
            if name == "/model":
                self._handle_model(args)
                return
            if name == "/workspace":
                self._handle_workspace(args)
                return
            if name in {"/runsdir", "/runs-dir"}:
                self._handle_runsdir(args)
                return
            if name in {"/codexbin", "/codex-bin"}:
                self._handle_codexbin(args)
                return
            if name in {"/syncback", "/sync-back"}:
                self._handle_syncback(args)
                return
            if name in {"/keepworkspaces", "/keep-workspaces"}:
                self._handle_keepworkspaces(args)
                return
            if name in {"/promptfile", "/prompt-file", "/runfile"}:
                self._handle_promptfile(args)
                return
            if name in {"/resumeinclude", "/resume-include", "/resume-include-non-interactive"}:
                self._handle_resumeinclude(args)
                return
            if name == "/resume":
                self._handle_resume(args)
                return
            self.status = f"Unknown command: {name}"
            self._sync()

        def _handle_kill(self, args: list[str]) -> None:
            if len(args) > 1:
                self.status = "Usage: /kill [agent]"
                self._sync()
                return
            if not self.running:
                self.status = "No active run"
                self._sync()
                return
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.status = "The run is already stopping"
                self._sync()
                return

            idx = self.selected_agent
            if args:
                match = re.fullmatch(r"(?:agent[-_])?(\d+)", args[0].strip(), re.IGNORECASE)
                if match is None:
                    self.status = "Usage: /kill [agent]"
                    self._sync()
                    return
                idx = int(match.group(1))
            pane = self.agents.get(idx)
            if pane is None:
                self.status = f"Unknown agent: {idx}"
                self._sync()
                return
            if pane.result is not None:
                self.status = f"AGENT-{idx:03d} has already finished"
                self._sync()
                return

            agent_cancel_event = self.agent_cancel_events.get(idx)
            if agent_cancel_event is None:
                self.status = f"AGENT-{idx:03d} cannot be stopped in this run"
                self._sync()
                return
            if agent_cancel_event.is_set():
                self.status = f"AGENT-{idx:03d} is already stopping"
                self._sync()
                return
            if pane.status != "running":
                self.status = f"AGENT-{idx:03d} is not running; queued agents will start normally"
                self._sync()
                return

            agent_cancel_event.set()
            pane.status = "stopping"
            self.status = f"Stopping AGENT-{idx:03d}; other agents continue"
            self._mark_detail_dirty(pane)
            self._sync()

        def _prepare_config_change(self, label: str) -> bool:
            if self.running:
                self.status = f"Cannot change {label} while running"
                self._sync()
                return False
            if self._has_pending_run():
                if getattr(self.args, "no_sync_back", False) or self.best_agent is None:
                    if not self._discard_pending_run():
                        self._sync()
                        return False
                elif not self._finalize_agent(self.selected_agent, archive_detail=True):
                    self._sync()
                    return False
            return True

        def _show_setting(self, text: str) -> None:
            self.status = text
            self._show_text(text)

        def _parse_bool_arg(self, args: list[str], label: str) -> bool | None:
            if not args:
                return None
            value = args[0].lower()
            if value in {"1", "yes", "y", "true", "on", "enable", "enabled"}:
                return True
            if value in {"0", "no", "n", "false", "off", "disable", "disabled"}:
                return False
            self.status = f"Usage: {label} <on|off>"
            self._sync()
            return None

        def _handle_numofagents(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"numofagents={self.num_agents}")
                return
            try:
                value = int(args[0])
            except ValueError:
                self.status = "Usage: /numofagents <positive integer>"
                self._sync()
                return
            if value <= 0:
                self.status = "numofagents must be > 0"
                self._sync()
                return
            if not self._prepare_config_change("agent count"):
                return
            self.num_agents = value
            self.args.num_agents = value
            self.selected_agent = min(self.selected_agent, value)
            self.agents = {idx: AgentPane(idx) for idx in range(1, value + 1)}
            self.best_agent = None
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"Next run will use {value} agents")

        def _handle_maxparallel(self, args: list[str]) -> None:
            current = getattr(self.args, "max_parallel", None)
            if not args:
                self._show_setting(f"maxparallel={current if current is not None else 'auto'}")
                return
            value_text = args[0].lower()
            if value_text in {"auto", "default", "clear", "none"}:
                value = None
            else:
                try:
                    value = int(value_text)
                except ValueError:
                    self.status = "Usage: /maxparallel <positive integer|auto>"
                    self._sync()
                    return
                if value <= 0:
                    self.status = "maxparallel must be > 0"
                    self._sync()
                    return
            if not self._prepare_config_change("max parallel"):
                return
            self.args.max_parallel = value
            if isinstance(value, int) and value > 1:
                self.args.serial = False
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"maxparallel={value if value is not None else 'auto'}")

        def _handle_execution(self, serial: bool) -> None:
            if not self._prepare_config_change("execution mode"):
                return
            self.args.serial = serial
            if not serial and getattr(self.args, "max_parallel", None) == 1:
                self.args.max_parallel = None
            self.run_info_rows = self._base_info_rows()
            self._show_setting("execution=serial" if serial else "execution=parallel")

        def _handle_bestby(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"bestby={getattr(self.args, 'best_by', 'reasoning_tokens')}")
                return
            try:
                value = normalize_best_by(args[0])
            except argparse.ArgumentTypeError as exc:
                self.status = str(exc)
                self._sync()
                return
            if not self._prepare_config_change("selection strategy"):
                return
            self.args.best_by = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"bestby={value}")

        def _handle_model(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"model={getattr(self.args, 'model', None) or 'default'}")
                return
            value = args[0]
            if value.lower() in {"clear", "default", "none"}:
                value = None
            if not self._prepare_config_change("model"):
                return
            self.args.model = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"model={value or 'default'}")

        def _handle_workspace(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"workspace={absolute_path_for_display(self.workspace)}")
                return
            workspace = Path(args[0]).expanduser().resolve()
            if not workspace.exists() or not workspace.is_dir():
                self.status = f"Workspace not found: {workspace}"
                self._sync()
                return
            if not self._prepare_config_change("workspace"):
                return
            self.workspace = workspace
            self.args.workspace = str(workspace)
            self.resume_session_id = ""
            self.resume_history_request += 1
            self._reset_conversation_detail()
            self.run_info_rows = self._base_info_rows()
            self._refresh_resume_control()
            self._show_setting(f"workspace={absolute_path_for_display(workspace)}")

        def _handle_runsdir(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"runsdir={getattr(self.args, 'runs_dir', None) or 'auto'}")
                return
            value = None if args[0].lower() in {"clear", "default", "auto", "none"} else str(Path(args[0]).expanduser())
            try:
                module_dir = Path(__file__).resolve().parent
                choose_run_base(default_run_anchor(module_dir, self.workspace), self.workspace, value)
            except SystemExit as exc:
                self.status = str(exc)
                self._sync()
                return
            if not self._prepare_config_change("runs dir"):
                return
            self.args.runs_dir = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"runsdir={value or 'auto'}")

        def _handle_codexbin(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"codexbin={getattr(self.args, 'codex_bin', 'codex')}")
                return
            if not self._prepare_config_change("codex binary"):
                return
            self.args.codex_bin = args[0]
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"codexbin={args[0]}")

        def _handle_syncback(self, args: list[str]) -> None:
            current = not bool(getattr(self.args, "no_sync_back", False))
            if not args:
                self._show_setting(f"syncback={'on' if current else 'off'}")
                return
            value = self._parse_bool_arg(args, "/syncback")
            if value is None:
                return
            if not self._prepare_config_change("sync back"):
                return
            self.args.no_sync_back = not value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"syncback={'on' if value else 'off'}")

        def _handle_keepworkspaces(self, args: list[str]) -> None:
            current = bool(getattr(self.args, "keep_workspaces", False))
            if not args:
                self._show_setting(f"keepworkspaces={'on' if current else 'off'}")
                return
            value = self._parse_bool_arg(args, "/keepworkspaces")
            if value is None:
                return
            if not self._prepare_config_change("keep workspaces"):
                return
            self.args.keep_workspaces = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"keepworkspaces={'on' if value else 'off'}")

        def _handle_promptfile(self, args: list[str]) -> None:
            if not args:
                self.status = "Usage: /promptfile <path>"
                self._sync()
                return
            if self.running:
                self.status = "Cannot run a prompt file while running"
                self._sync()
                return
            path = Path(args[0]).expanduser()
            try:
                prompt = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                self.status = f"Cannot read prompt file: {exc}"
                self._sync()
                return
            if not prompt:
                self.status = "Prompt file is empty"
                self._sync()
                return
            self._start_run(prompt)

        def _handle_resumeinclude(self, args: list[str]) -> None:
            current = bool(getattr(self.args, "resume_include_non_interactive", True))
            if not args:
                self._show_setting(f"resumeinclude={'on' if current else 'off'}")
                return
            value = self._parse_bool_arg(args, "/resumeinclude")
            if value is None:
                return
            if self.running:
                self.status = "Cannot change resume include mode while running"
                self._sync()
                return
            self.args.resume_include_non_interactive = value
            self._show_setting(f"resumeinclude={'on' if value else 'off'}")

        def _reset_conversation_detail(self) -> None:
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.selected_agent = min(self.selected_agent, self.num_agents)
            self.best_agent = None
            self.detail_history.clear()
            self.command_history.clear()
            self._mark_detail_dirty()

        def _history_detail_block(self, entry: CodexHistoryEntry) -> tuple[str, str, str]:
            if entry.category == "user":
                return ">", entry.text, "cyan"
            if entry.category == "thought":
                return "·", entry.text, "dim white"
            return "✓", entry.text, "green"

        def _select_resume_session(self, session_id: str, rollout_path: str = "") -> None:
            session_id = session_id.strip()
            self.resume_history_request += 1
            request_id = self.resume_history_request
            self.status = f"Loading resume session: {session_id}"
            self._sync()

            def target() -> None:
                entries: list[CodexHistoryEntry] = []
                load_error = ""
                rejected = False
                try:
                    with self._resume_io_lock:
                        load_error = subagent_resume_error(get_codex_home(), session_id) or ""
                        rejected = bool(load_error)
                        if not rejected:
                            entries = load_codex_session_history(
                                get_codex_home(),
                                session_id,
                                rollout_path,
                            )
                except Exception as exc:  # noqa: BLE001
                    load_error = str(exc)
                try:
                    self.call_from_thread(
                        self.post_message,
                        ResumeHistoryLoaded(
                            request_id,
                            session_id,
                            entries,
                            load_error,
                            rejected,
                        ),
                    )
                except RuntimeError:
                    pass

            threading.Thread(target=target, name="pcr-resume-history", daemon=True).start()

        def _handle_resume(self, args: list[str]) -> None:
            if self.running:
                self.status = "Cannot change resume session while running"
                self._sync()
                return
            if args and args[0].lower() in {"clear", "new"}:
                if self._has_pending_run():
                    if getattr(self.args, "no_sync_back", False) or self.best_agent is None:
                        if not self._discard_pending_run():
                            self._sync()
                            return
                    elif not self._finalize_agent(self.selected_agent, require_resume=False, archive_detail=True):
                        self._sync()
                        return
                self.resume_session_id = ""
                self.resume_history_request += 1
                self.pending_resume_selector = None
                self._reset_conversation_detail()
                self.run_info_rows = self._base_info_rows()
                self._refresh_resume_control()
                self.status = "Resume cleared"
                self._sync()
                return

            selector = args[0] if args else "list"
            needs_choices = selector.lower() in {"list", "latest"} or selector.isdigit()
            if needs_choices and not self.resume_choices_loaded:
                self.pending_resume_selector = selector
                self.status = "Loading resume sessions"
                self._refresh_resume_control()
                self._sync()
                return
            self._handle_loaded_resume_selector(selector)

        def _handle_loaded_resume_selector(self, selector: str) -> None:
            if selector.lower() == "list":
                self._show_resume_list()
                return
            selector = "1" if selector.lower() == "latest" else selector
            chosen = None
            if selector.isdigit():
                idx = int(selector)
                if 1 <= idx <= len(self.resume_entries):
                    chosen = self.resume_entries[idx - 1]
            else:
                for session in self.resume_entries:
                    if session.session_id == selector:
                        chosen = session
                        break
                if chosen is None:
                    if not self._prepare_config_change("resume session"):
                        return
                    self._select_resume_session(selector)
                    return
            if chosen is None:
                self.status = "No resumable session found"
                self._sync()
                return
            if not self._prepare_config_change("resume session"):
                return
            self._select_resume_session(chosen.session_id, chosen.rollout_path)

        def _show_resume_list(self) -> None:
            lines = ["Recent sessions:", "Use /resume <number> or /resume <session_id> to load one.", ""]
            for index, session in enumerate(self.resume_entries[:8], 1):
                lines.append(f"{index}. {session.session_id}  {session.title[:80]}")
            self.status = "Choose a resume session" if len(lines) > 3 else "No resumable sessions found"
            self._show_text("\n".join(lines) if len(lines) > 3 else "No resumable sessions found")

        def _show_status(self) -> None:
            self._show_text(self._tree_text())

        def _start_run(self, prompt: str) -> bool:
            if self.running:
                return self._queue_prompt_from_finished_agent(prompt)
            if not self._commit_runner_inputs():
                self._sync()
                return False
            if self._has_pending_run() and (getattr(self.args, "no_sync_back", False) or self.best_agent is None):
                if not self._discard_pending_run():
                    self._sync()
                    return False
            if self._has_pending_run() and not self._finalize_agent(self.selected_agent, archive_detail=True):
                self._sync()
                return False
            self._archive_command_history()
            self.running = True
            self.exit_after_run = False
            self.cancel_event = threading.Event()
            self.best_agent = None
            self.started_at = time.monotonic()
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.agent_cancel_events = {
                idx: threading.Event() for idx in range(1, self.num_agents + 1)
            }
            for pane in self.agents.values():
                pane.input_text = prompt
            self.selected_agent = min(self.selected_agent, self.num_agents)
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = "Preparing agents"
            self._sync()

            run_args = argparse.Namespace(**vars(self.args))
            run_args.prompt = prompt
            run_args.prompt_file = None
            run_args.num_agents = self.num_agents
            run_args.max_parallel = min(run_args.max_parallel or self.num_agents, self.num_agents)
            run_args.resume = False
            run_args.resume_session_id = self.resume_session_id or None
            # The TUI owns selection, sync-back, and cleanup after run_once returns.
            run_args.no_sync_back = True
            run_args.keep_workspaces = True
            run_args.cancel_event = self.cancel_event
            run_args.agent_cancel_events = self.agent_cancel_events

            def target() -> None:
                previous_disable = logging.root.manager.disable
                try:
                    logging.disable(logging.CRITICAL)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        run_once(run_args, prompt, progress_callback=self._post_progress, print_output=False)
                except BaseException as exc:  # noqa: BLE001
                    self._post_progress({"type": "run_failed", "message": str(exc)})
                finally:
                    logging.disable(previous_disable)

            self.runner_thread = threading.Thread(target=target, name="pcr-tui-runner", daemon=False)
            self.runner_thread.start()
            return True

        def _queue_prompt_from_finished_agent(self, prompt: str) -> bool:
            if self.queued_prompt:
                self.status = "Already stopping remaining agents"
                self._sync()
                return False
            if getattr(self.args, "no_sync_back", False):
                self.status = "Cannot continue a running agent while sync back is disabled"
                self._sync()
                return False
            pane = self.agents.get(self.selected_agent)
            result = pane.result if pane is not None else None
            if not isinstance(result, dict) or result.get("status") != "success":
                self.status = f"AGENT-{self.selected_agent:03d} has not finished successfully"
                self._sync()
                return False
            self.queued_prompt = prompt
            self.queued_agent = self.selected_agent
            if self.cancel_event is not None:
                self.cancel_event.set()
            self.status = f"Stopping remaining agents; continuing from AGENT-{self.selected_agent:03d}"
            self._sync()
            return True

        def _continue_queued_prompt(self) -> None:
            prompt = self.queued_prompt
            agent_idx = self.queued_agent
            self.queued_prompt = ""
            self.queued_agent = None
            if not prompt or agent_idx is None:
                return
            if not self._finalize_agent(agent_idx, archive_detail=True):
                try:
                    editor = self.query_one("#prompt", PromptEditor)
                    editor.text = prompt
                    self._sync_prompt_height()
                    editor.focus()
                except Exception:
                    pass
                return
            self._archive_command_history()
            if not self._start_run(prompt):
                try:
                    editor = self.query_one("#prompt", PromptEditor)
                    editor.text = prompt
                    self._sync_prompt_height()
                    editor.focus()
                except Exception:
                    pass

        def _post_progress(self, payload: dict[str, Any]) -> None:
            if payload.get("type") == "run_prepared":
                rows = payload.get("rows")
                if isinstance(rows, list):
                    self._remember_run_paths(
                        [(str(key), str(value)) for key, value in rows if isinstance(key, str)]
                    )
            try:
                self.call_from_thread(self.post_message, RunnerEvent(payload))
            except RuntimeError:
                pass

        def _remember_run_paths(self, rows: list[tuple[str, str]]) -> None:
            data = dict(rows)
            if data.get("RUNS_ROOT"):
                self.pending_run_root = Path(data["RUNS_ROOT"])
            if data.get("WORKSPACE COPIES"):
                self.pending_workspaces_root = Path(data["WORKSPACE COPIES"])

        def _has_pending_run(self) -> bool:
            return self.pending_workspaces_root is not None

        def _clear_pending_run(self) -> None:
            self.pending_run_root = None
            self.pending_workspaces_root = None

        def _discard_pending_run(self) -> bool:
            if not self._cleanup_after_pending_run():
                return False
            self._clear_pending_run()
            self.best_agent = None
            self.run_info_rows = self._base_info_rows()
            return True

        def _cleanup_after_pending_run(self) -> bool:
            if getattr(self.args, "keep_workspaces", False):
                return True
            root = self.pending_workspaces_root
            if root is None:
                return True
            try:
                if is_relative_to(root, self.workspace):
                    raise RuntimeError(f"refusing to delete workspace-owned path: {root}")
                cleanup_workspace_copies(self.workspace, root)
                return True
            except Exception as exc:  # noqa: BLE001
                self.status = f"Cleanup failed: {exc}"
                return False

        def _shutdown_runner_and_cleanup(self) -> None:
            """Stop an active runner and remove copies after the UI loop exits."""
            if self._shutdown_cleanup_started:
                return
            self._shutdown_cleanup_started = True

            if self.cancel_event is not None:
                self.cancel_event.set()
            if self.runner_thread is not None and self.runner_thread.is_alive():
                self.runner_thread.join()
            self.running = False

            if self._has_pending_run():
                if not self._cleanup_after_pending_run():
                    raise RuntimeError(self.status)
                self._clear_pending_run()

        def _finalize_agent(self, idx: int, require_resume: bool = True, archive_detail: bool = False) -> bool:
            pane = self.agents.get(idx)
            result_data = pane.result if pane is not None else None
            if not result_data:
                self.status = f"AGENT-{idx:03d} has no finished result"
                return False
            result = AgentResult(**result_data)
            if result.status != "success":
                self.status = f"AGENT-{idx:03d} is not successful"
                return False
            try:
                if require_resume and not result.codex_thread_id:
                    source_codex_home = Path(result.codex_home).expanduser().resolve() if result.codex_home else get_codex_home()
                    result.codex_thread_id = infer_codex_thread_id_for_result(result, source_codex_home)
                    if not result.codex_thread_id:
                        self.status = f"AGENT-{idx:03d} has no resumable session"
                        return False

                sync_best_workspace_back(Path(result.workspace_dir), self.workspace)

                promotion = None
                try:
                    promotion = promote_best_codex_session_to_workspace(result, self.workspace)
                except Exception:
                    if require_resume:
                        raise
                if require_resume:
                    if promotion is None or not result.codex_thread_id:
                        self.status = f"AGENT-{idx:03d} has no resumable session"
                        return False
                    promotion_error = getattr(promotion, "error", None)
                    if promotion_error:
                        self.status = f"AGENT-{idx:03d} session promotion failed: {promotion_error}"
                        return False
                if result.codex_thread_id:
                    result_data["codex_thread_id"] = result.codex_thread_id
                    if require_resume:
                        self.resume_session_id = result.codex_thread_id
                if not self._cleanup_after_pending_run():
                    return False
                if archive_detail and pane is not None:
                    self._archive_agent_detail(pane)
                    pane.input_text = ""
                    pane.final_text = ""
                    pane.clear_detail()
                    self._mark_detail_dirty(pane)
                self._clear_pending_run()
                self.run_info_rows = self._base_info_rows()
                self._refresh_resume_control()
                self.status = f"Continuing from AGENT-{idx:03d}"
                return True
            except Exception as exc:  # noqa: BLE001
                self.status = f"Cannot continue AGENT-{idx:03d}: {exc}"
                return False

        def _show_text(self, text: str) -> None:
            text = text.strip()
            if text:
                self.command_history.append(("•", text, "yellow"))
            self._mark_detail_dirty()
            self._sync()

        def _read_final_message(self, result: dict[str, Any]) -> str:
            path = result.get("final_message")
            if not isinstance(path, str) or not path:
                return ""
            try:
                text = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                return ""
            return compact_block(text)

        def _tick(self) -> None:
            if self.running and self.started_at is not None:
                self.work_frame += 1
                if self.queued_prompt and self.queued_agent is not None:
                    self.status = (
                        f"Stopping remaining agents; continuing from "
                        f"AGENT-{self.queued_agent:03d} {self._pulse()}"
                    )
                else:
                    self.status = f"Working {int(time.monotonic() - self.started_at)}s {self._pulse()}"
                self._sync()

        @property
        def current_tip(self) -> str:
            return TUI_TIPS[self.tip_index]

        @property
        def current_tip_icon(self) -> str:
            return TIP_ICON

        @property
        def current_tip_icon_color(self) -> str:
            return TIP_ICON_COLORS[self.tip_icon_index]

        def _tip_renderable(self) -> Content:
            return Content.assemble(
                (f"{self.current_tip_icon}  ", f"bold {self.current_tip_icon_color}"),
                self.current_tip,
            )

        def _refresh_tip(self) -> None:
            if self._selection_drag_active():
                self._tip_refresh_deferred_for_selection = True
                return
            try:
                self.query_one("#tips", Static).update(self._tip_renderable(), layout=False)
            except Exception:
                return
            self._tip_refresh_deferred_for_selection = False

        def _advance_tip(self) -> None:
            self.tip_index = (self.tip_index + 1) % len(TUI_TIPS)
            self._refresh_tip()

        def _advance_tip_icon(self) -> None:
            self.tip_icon_index = (self.tip_icon_index + 1) % len(TIP_ICON_COLORS)
            self._refresh_tip()

        def _sync(self) -> None:
            if self._selection_drag_active():
                self._sync_deferred_for_selection = True
                return
            try:
                self.query_one("#runner-frame", Vertical)
            except Exception:
                return
            self._sync_deferred_for_selection = False
            self._sync_runner_panel()
            frame = self.query_one("#detail-frame", Vertical)
            pane = self.agents.get(self.selected_agent)
            detail_visible = self._has_detail_content(pane)
            frame.display = detail_visible
            border_style = "green" if pane is not None and pane.idx == self.best_agent else "cyan"
            frame.styles.border = ("round", border_style)
            frame.border_title = self._detail_title(pane) if detail_visible and pane is not None else ""

            scroll = self.query_one("#detail-scroll", DetailScroll)
            should_follow_end = scroll.follow_tail
            detail = self.query_one("#detail", Static)
            cache_key_before = self._detail_cache_key
            detail_renderable = self._detail_renderable()
            detail_changed = self._detail_cache_key != cache_key_before
            if detail_changed:
                detail.update(detail_renderable)
            if detail_visible and detail_changed and should_follow_end:
                self.call_after_refresh(
                    scroll.follow_end_if_enabled,
                    animate=False,
                    immediate=True,
                )
            self.query_one("#state", Static).update(self._state_text())

        def _refresh_resume_control(self) -> None:
            if not self.is_running:
                return
            workspace = self.workspace
            if self.resume_choices_inflight is not None:
                _inflight_id, inflight_workspace = self.resume_choices_inflight
                if inflight_workspace == workspace:
                    return
            self.resume_choices_request += 1
            request_id = self.resume_choices_request
            self.resume_choices_inflight = (request_id, workspace)
            self.resume_choices_loaded = False
            include_non_interactive = bool(
                getattr(self.args, "resume_include_non_interactive", True)
            )

            def target() -> None:
                entries: list[Any] = []
                error = ""
                try:
                    with self._resume_io_lock:
                        entries = list_resume_sessions(
                            workspace,
                            include_non_interactive=include_non_interactive,
                        )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                try:
                    self.call_from_thread(
                        self.post_message,
                        ResumeChoicesLoaded(request_id, workspace, entries, error),
                    )
                except RuntimeError:
                    pass

            threading.Thread(target=target, name="pcr-resume-choices", daemon=True).start()

        def _set_select_control(
            self,
            control: Select,
            value: Any,
            options: list[tuple[Any, Any]] | None = None,
        ) -> None:
            # Select posts Changed from its reactive watcher. Prevent it at the
            # control so delayed programmatic events cannot look like user input.
            with control.prevent(Select.Changed):
                if options is not None:
                    control.set_options(options)
                if control.value != value:
                    control.value = value

        def _apply_resume_choices(self, entries: list[Any]) -> None:
            self.resume_entries = entries
            choices = resume_select_options(entries, self.resume_session_id)
            options_changed = choices != self.resume_choices
            self.resume_choices = choices
            try:
                control = self.query_one("#config-resume", Select)
            except Exception:
                return
            self._updating_controls = True
            try:
                self._set_select_control(
                    control,
                    self.resume_session_id,
                    self.resume_choices if options_changed else None,
                )
            finally:
                self._updating_controls = False

        def _sync_runner_panel(self) -> None:
            rows = dict(self._tree_rows())
            static_rows = {
                "CONVERSATION": "#runner-conversation",
                "WORKSPACE": "#runner-workspace",
                "RUNS_ROOT": "#runner-runs-root",
                "CODEX_BIN": "#runner-codex-bin",
            }
            for label, selector in static_rows.items():
                self.query_one(selector, Static).update(rows.get(label, ""))

            best_key = self.query_one("#runner-best-agent-key", Static)
            best_value = self.query_one("#runner-best-agent", Static)
            best_visible = self.best_agent is not None
            best_key.display = best_visible
            best_value.display = best_visible
            if best_visible:
                best_value.update(f"agent_{self.best_agent:03d}")

            current_model = str(getattr(self.args, "model", None) or "")
            model_options = None
            if current_model not in {value for _label, value in self.model_choices}:
                self.model_choices = codex_model_options(current_model)
                model_options = self.model_choices
            resume_options = None
            if self.resume_session_id not in {value for _label, value in self.resume_choices}:
                self.resume_choices = resume_select_options(
                    self.resume_entries,
                    self.resume_session_id,
                )
                resume_options = self.resume_choices

            execution_select = self.query_one("#config-execution", Select)
            best_by_select = self.query_one("#config-best-by", Select)
            model_select = self.query_one("#config-model", Select)
            sync_back_select = self.query_one("#config-sync-back", Select)
            keep_workspaces_select = self.query_one("#config-keep-workspaces", Select)
            resume_select = self.query_one("#config-resume", Select)
            controls = [
                self.query_one("#config-agents", Input),
                self.query_one("#config-max-parallel", Input),
                execution_select,
                best_by_select,
                model_select,
                sync_back_select,
                keep_workspaces_select,
                resume_select,
            ]
            self._updating_controls = True
            try:
                agents_input = self.query_one("#config-agents", Input)
                if self.focused is not agents_input:
                    agents_value = str(self.num_agents)
                    agents_input.value = agents_value
                    self._committed_input_values["config-agents"] = agents_value
                max_parallel_input = self.query_one("#config-max-parallel", Input)
                if self.focused is not max_parallel_input:
                    max_parallel_value = rows.get("MAX_PARALLEL", "")
                    max_parallel_input.value = max_parallel_value
                    self._committed_input_values["config-max-parallel"] = max_parallel_value
                self._set_select_control(
                    execution_select,
                    rows.get("EXECUTION", "parallel"),
                )
                self._set_select_control(
                    best_by_select,
                    str(getattr(self.args, "best_by", "reasoning_tokens")),
                )
                self._set_select_control(model_select, current_model, model_options)
                self._set_select_control(
                    sync_back_select,
                    not bool(getattr(self.args, "no_sync_back", False)),
                )
                self._set_select_control(
                    keep_workspaces_select,
                    bool(getattr(self.args, "keep_workspaces", False)),
                )
                self._set_select_control(
                    resume_select,
                    self.resume_session_id,
                    resume_options,
                )
                for control in controls:
                    control.disabled = self.running
            finally:
                self._updating_controls = False

        def _tree_renderable(self) -> Panel:
            table = Table.grid(padding=(0, 2))
            table.add_column(style="bold")
            table.add_column()
            for label, value in self._tree_rows():
                table.add_row(label, value)
            return Panel(table, title=Text("PARALLEL-CODEX-RUNNER", style="bold"), border_style="cyan")

        def _tree_text(self) -> str:
            rows = self._tree_rows()
            label_width = max(len(label) for label, _value in rows)
            return "\n".join(f"{label:<{label_width}}  {value}" for label, value in rows)

        def _tree_rows(self) -> list[tuple[str, str]]:
            rows: list[tuple[str, str]] = [
                ("CONVERSATION", self.status),
                *self.run_info_rows,
            ]
            if self.best_agent is not None:
                rows.append(("BEST AGENT", f"agent_{self.best_agent:03d}"))
            return rows

        def _visible_info_rows(self, rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
            hidden = {"MODULE_DIR", "RUN_ANCHOR", "METADATA", "WORKSPACE COPIES"}
            return [(label, value) for label, value in rows if label not in hidden]

        def _base_info_rows(self) -> list[tuple[str, str]]:
            max_parallel = (
                1
                if getattr(self.args, "serial", False)
                else min(getattr(self.args, "max_parallel", None) or self.num_agents, self.num_agents)
            )
            execution = "serial" if max_parallel == 1 else "parallel"
            module_dir = Path(__file__).resolve().parent
            run_anchor = default_run_anchor(module_dir, self.workspace)
            run_base = choose_run_base(run_anchor, self.workspace, getattr(self.args, "runs_dir", None))
            return [
                ("WORKSPACE", absolute_path_for_display(self.workspace)),
                ("RUNS_ROOT", f"pending under {absolute_path_for_display(run_base)}"),
                ("AGENTS", str(self.num_agents)),
                ("EXECUTION", execution),
                ("MAX_PARALLEL", str(max_parallel)),
                ("BEST_BY", str(getattr(self.args, "best_by", "reasoning_tokens"))),
                ("MODEL", str(getattr(self.args, "model", None) or "default")),
                ("CODEX_BIN", str(getattr(self.args, "codex_bin", "codex"))),
                ("SYNC_BACK", "NO" if getattr(self.args, "no_sync_back", False) else "YES"),
                ("KEEP_WORKSPACES", "YES" if getattr(self.args, "keep_workspaces", False) else "NO"),
                ("RESUME", self.resume_session_id or "NO"),
            ]

        def _detail_text(self) -> str:
            pane = self.agents.get(self.selected_agent)
            if pane is None:
                return ""
            blocks = self._detail_blocks(pane)
            if not blocks:
                return ""
            return "\n\n".join(self._format_prefixed_block(prefix, text) for prefix, text, _style in blocks)

        def _detail_renderable(self) -> object:
            pane = self.agents.get(self.selected_agent)
            if pane is None:
                self._detail_cache_key = ("none", self.selected_agent, self.detail_revision)
                self._detail_cache_renderable = ""
                return ""
            key = self._detail_cache_key_for(pane)
            if key == self._detail_cache_key:
                return self._detail_cache_renderable

            blocks = self._detail_blocks(pane)
            if not blocks:
                self._detail_cache_key = key
                self._detail_cache_renderable = ""
                return ""
            parts: list[str | tuple[str, str]] = []
            for idx, (prefix, block, style) in enumerate(blocks):
                if idx:
                    parts.append("\n\n")
                parts.append((self._format_prefixed_block(prefix, block), style))
            renderable = Content.assemble(*parts)
            self._detail_cache_key = key
            self._detail_cache_renderable = renderable
            return renderable

        def _archive_agent_detail(self, pane: AgentPane) -> None:
            self.detail_history.extend(self._pane_detail_blocks(pane))
            self._mark_detail_dirty()

        def _archive_command_history(self) -> None:
            if self.command_history:
                self.detail_history.extend(self.command_history)
                self.command_history.clear()
                self._mark_detail_dirty()

        def _mark_detail_dirty(self, pane: AgentPane | None = None) -> None:
            if pane is None:
                self.detail_revision += 1
                self._detail_cache_key = None
            else:
                pane.revision += 1
                if pane.idx == self.selected_agent:
                    self._detail_cache_key = None

        def _detail_cache_key_for(self, pane: AgentPane) -> tuple[Any, ...]:
            pulse = self.work_frame if pane.status == "running" and not pane.thought_lines and not pane.output_lines else None
            return (pane.idx, pane.revision, self.detail_revision, self._detail_content_width(), pulse)

        def _detail_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
            return [*self.detail_history, *self._pane_detail_blocks(pane), *self.command_history]

        def _has_detail_content(self, pane: AgentPane | None) -> bool:
            return pane is not None and bool(self._detail_blocks(pane))

        def _pane_detail_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
            blocks: list[tuple[str, str, str]] = []
            if pane.input_text:
                blocks.append((">", pane.input_text, "cyan"))
            thoughts = "\n".join(pane.thought_lines)
            if thoughts:
                blocks.append(("·", thoughts, "dim white"))
            elif pane.status == "running" and not pane.output_lines:
                blocks.append(("·", self._pulse(), "dim white"))
            output_lines = [
                line
                for line in pane.output_lines
                if not (pane.final_text and same_display_message(line, pane.final_text))
            ]
            output = "\n".join(output_lines)
            if output:
                blocks.append(("◇", output, "white"))
            activity = "\n".join(pane.lines)
            if activity:
                blocks.append(("•", activity, "dim white"))
            if pane.final_text:
                blocks.append(("✓", pane.final_text, "green"))
            return blocks

        def _detail_content_width(self) -> int:
            try:
                detail = self.query_one("#detail", Static)
                width = int(getattr(detail.content_size, "width", 0) or getattr(detail.size, "width", 0) or 0)
            except Exception:
                width = 0
            if width <= 0:
                try:
                    width = int(getattr(self.size, "width", 0) or 0) - 4
                except Exception:
                    width = 0
            return max(20, width or 80)

        def _format_prefixed_block(self, prefix: str, block: str) -> str:
            prefix_width = cell_len(prefix) + 1
            content_width = max(8, self._detail_content_width() - prefix_width - 4)
            folded = fold_text_by_cells(block, content_width)
            indent = " " * prefix_width
            return f"{prefix} {folded.replace(chr(10), chr(10) + indent)}"

        def _detail_title(self, pane: AgentPane) -> str:
            title = f"AGENT-{pane.idx:03d}"
            parts = []
            if pane.status in {"stopping", "killed"}:
                parts.append(pane.status)
            if isinstance(pane.result, dict):
                seconds = format_seconds(pane.result.get("seconds"))
                if seconds:
                    parts.append(f"seconds={seconds}")
            if pane.reasoning_tokens is not None:
                parts.append(f"reasoning_tokens={pane.reasoning_tokens}")
            if pane.idx == self.best_agent:
                parts.append("best")
            parts.append("←/→ switch")
            return f"{title}, {', '.join(parts)}"

        def _state_text(self) -> str:
            parts = [self.status]
            if summary := self._agent_summary():
                parts.append(summary)
            parts.extend(["Ctrl-C copy/clear/exit", "←/→ switch"])
            return " | ".join(parts)

        def _update_suggestions(self, value: str) -> None:
            suggestions = command_suggestions(value)
            self.suggestion_line_count = len(suggestions)
            try:
                widget = self.query_one("#suggestions", Static)
            except Exception:
                return
            widget.update("\n".join(suggestions))
            widget.styles.height = min(self.suggestion_line_count, MAX_SUGGESTIONS)

        def _prompt_content_width(self) -> int:
            try:
                prompt = self.query_one("#prompt", PromptEditor)
                width = int(getattr(prompt, "wrap_width", 0) or 0)
                if width > 0:
                    return width
                width = int(getattr(prompt.content_size, "width", 0) or getattr(prompt.size, "width", 0) or 0)
            except Exception:
                width = 0
            return max(20, width or 80)

        def _sync_prompt_height(self) -> None:
            try:
                text = self.query_one("#prompt", PromptEditor).text
                width = self._prompt_content_width()
                visible_lines = sum(max(1, (cell_len(line) + width - 1) // width) for line in (text or "").split("\n"))
                self.prompt_height = max(3, min(20, visible_lines + 2))
                self.query_one("#prompt", PromptEditor).styles.height = self.prompt_height
            except Exception:
                return

        def _agent_summary(self) -> str:
            counts: dict[str, int] = {}
            for pane in self.agents.values():
                if pane.status == "idle":
                    continue
                counts[pane.status] = counts.get(pane.status, 0) + 1
            return " · ".join(f"{count} {status}" for status, count in sorted(counts.items()))

        def _pulse(self) -> str:
            pulses = ("▰▱▱", "▰▰▱", "▰▰▰", "▱▰▰", "▱▱▰", "▱▱▱")
            return pulses[self.work_frame % len(pulses)]


    def run_textual_tui(args: argparse.Namespace) -> int:
        app = PcrTextualApp(args)
        try:
            app.run()
        finally:
            app._shutdown_runner_and_cleanup()
        return 0
