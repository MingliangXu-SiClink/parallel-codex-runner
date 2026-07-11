from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import re
import shlex
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
    ("/numofagents <n>", "same as -n / --num-agents"),
    ("/maxparallel <n|auto>", "same as --max-parallel"),
    ("/serial", "same as --serial"),
    ("/parallel", "clear --serial"),
    ("/bestby <duration|reasoning_tokens>", "same as --best-by"),
    ("/model <name|clear>", "same as --model"),
    ("/workspace <path>", "same as --workspace"),
    ("/runsdir <path|clear>", "same as --runs-dir"),
    ("/codexbin <path>", "same as --codex-bin"),
    ("/syncback <on|off>", "toggle --no-sync-back"),
    ("/keepworkspaces <on|off>", "toggle --keep-workspaces"),
    ("/promptfile <path>", "same as --prompt-file, then run"),
    ("/resumeinclude <on|off>", "toggle --resume-include-non-interactive"),
    ("/resume", "show resumable Codex sessions"),
    ("/resume <n|session>", "same as --resume-session-id"),
    ("/resume latest", "load latest session"),
    ("/resume clear", "start without resume"),
    ("/clear", "clear the current view"),
    ("/exit", "quit"),
)
MAX_SUGGESTIONS = 24


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
            'Enter normal text to run the same as: pcr "prompt"',
            "Most option commands apply to the next run.",
            "If a completed run is pending, PCR finalizes the selected agent before changing config.",
            "Ctrl-C clears a non-empty prompt; Ctrl-C on an empty prompt exits.",
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


def compact_output_line(line: str, max_chars: int = 1000) -> str:
    if len(line) <= max_chars:
        return line
    keep = max(1, (max_chars - 5) // 2)
    return f"{line[:keep].rstrip()} ... {line[-keep:].lstrip()}"


def compact_command_output(output: str) -> str:
    lines = output.rstrip().splitlines()
    if len(lines) <= 3:
        return "\n".join(compact_output_line(line) for line in lines)
    return "\n".join([compact_output_line(lines[0]), compact_output_line(lines[1]), "...", compact_output_line(lines[-1])])


def command_status_suffix(status: str, exit_code: Any) -> str:
    if exit_code is not None:
        return f" [exit {exit_code}]"
    if status and status != "in_progress":
        return f" [{status}]"
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
        output = text_value(item.get("aggregated_output"))
        status = text_value(item.get("status"))
        exit_code = item.get("exit_code")
        if output:
            header = f"$ {command}" if command else "$ command"
            header += command_status_suffix(status, exit_code)
            return "activity", f"{header}\n{compact_command_output(output)}"
        if command:
            suffix = command_status_suffix(status, exit_code)
            return "activity", f"$ {command}{suffix}"

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
    from textual.containers import Vertical, VerticalScroll
    from textual.message import Message
    from textual.widgets import Static, TextArea
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


    @dataclass
    class AgentPane:
        idx: int
        status: str = "idle"
        reasoning_tokens: int | None = None
        input_text: str = ""
        final_text: str = ""
        command_text: str = ""
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
        ) -> None:
            super().__init__()
            self.request_id = request_id
            self.session_id = session_id
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
        #tree {
            height: auto;
            margin: 0 1;
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
            ("ctrl+c", "interrupt_or_exit", "Exit"),
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
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.selected_agent = 1
            self.running = False
            self.status = "Ready"
            self.best_agent: int | None = None
            self.started_at: float | None = None
            self.prompt_height = 3
            self.suggestion_line_count = 0
            self.work_frame = 0
            self.exit_after_run = False
            self.cancel_event: threading.Event | None = None
            self.run_info_rows = self._base_info_rows()
            self.pending_run_root: Path | None = None
            self.pending_workspaces_root: Path | None = None
            self.detail_history: list[tuple[str, str, str]] = []
            self.detail_revision = 0
            self.resume_history_request = 0
            self._detail_cache_key: tuple[Any, ...] | None = None
            self._detail_cache_renderable: object = ""

        def compose(self) -> ComposeResult:
            with Vertical(id="root"):
                yield Static("", id="tree")
                with Vertical(id="detail-frame"):
                    with VerticalScroll(id="detail-scroll"):
                        yield Static("", id="detail")
                yield Static("", id="suggestions")
                yield PromptEditor(
                    "",
                    id="prompt",
                    soft_wrap=True,
                    show_line_numbers=False,
                    placeholder="输入需求，或输入 / 查看命令",
                )
                yield Static("", id="state")

        def on_mount(self) -> None:
            self.set_interval(0.25, self._tick)
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
            prompt.clear()
            self._update_suggestions("")
            self._sync_prompt_height()
            if not value:
                return
            if value.startswith("/"):
                self._handle_command(value)
            else:
                self._start_run(value)
            prompt.focus()

        @on(TextArea.Changed)
        def _on_text_changed(self, event: TextArea.Changed) -> None:
            if event.text_area.id == "prompt":
                self._update_suggestions(event.text_area.text)
                self._sync_prompt_height()

        @on(AgentSwitchRequested)
        def _on_switch(self, event: AgentSwitchRequested) -> None:
            self._switch_agent(event.delta)

        async def _on_key(self, event: events.Key) -> None:
            if event.key in {"left", "right"}:
                prompt = self.query_one("#prompt", PromptEditor)
                if self.focused is not prompt and not prompt.text.strip():
                    event.stop()
                    event.prevent_default()
                    self._switch_agent(-1 if event.key == "left" else 1)
                    prompt.focus()
                    return
            await super()._on_key(event)

        def _switch_agent(self, delta: int) -> None:
            self.selected_agent = min(self.num_agents, max(1, self.selected_agent + delta))
            self._sync()

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
                pane.status = str(payload.get("status") or pane.status)
                self._mark_detail_dirty(pane)
            elif kind == "agent_started" and pane is not None:
                pane.status = "running"
                self._mark_detail_dirty(pane)
            elif kind == "agent_tokens" and pane is not None:
                value = payload.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
            elif kind == "agent_line" and pane is not None:
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
                final_text = self._read_final_message(result)
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
                    self.status = "Cancelled"
                    if self._cleanup_after_pending_run():
                        self._clear_pending_run()
                else:
                    self.status = f"Done: agent_{self.best_agent:03d}" if self.best_agent else "No successful agent"
            elif kind == "run_failed":
                self.running = False
                self.cancel_event = None
                self.status = f"Run failed: {payload.get('message') or ''}"
                if self._cleanup_after_pending_run():
                    self._clear_pending_run()
            if kind not in {"agent_line", "agent_tokens", "agent_status"}:
                self._sync()
            if kind in {"run_finished", "run_failed"} and self.exit_after_run and not self._has_pending_run():
                self.exit()

        @on(ResumeHistoryLoaded)
        def _on_resume_history_loaded(self, event: ResumeHistoryLoaded) -> None:
            if event.request_id != self.resume_history_request or event.session_id != self.resume_session_id:
                return
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
                    for pane in self.agents.values():
                        pane.command_text = ""
                    self._mark_detail_dirty()
                    self.status = "Cleared command view"
                    self._sync()
                    return
            for pane in self.agents.values():
                pane.status = "idle"
                pane.reasoning_tokens = None
                pane.input_text = ""
                pane.final_text = ""
                pane.command_text = ""
                pane.result = None
                pane.clear_detail()
            self.best_agent = None
            self.resume_history_request += 1
            self.detail_history.clear()
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = "Ready"
            self._sync()

        def action_interrupt_or_exit(self) -> None:
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
            self._mark_detail_dirty()

        def _history_detail_block(self, entry: CodexHistoryEntry) -> tuple[str, str, str]:
            if entry.category == "user":
                return ">", entry.text, "cyan"
            if entry.category == "thought":
                return "·", entry.text, "dim white"
            return "✓", entry.text, "green"

        def _select_resume_session(self, session_id: str, rollout_path: str = "") -> None:
            error = subagent_resume_error(get_codex_home(), session_id)
            if error:
                self.resume_session_id = ""
                self.status = error
                self.run_info_rows = self._base_info_rows()
                self._sync()
                return

            self.resume_session_id = session_id
            self.resume_history_request += 1
            request_id = self.resume_history_request
            self._reset_conversation_detail()
            self.run_info_rows = self._base_info_rows()
            self.status = f"Loading resume session: {session_id}"
            self._sync()

            def target() -> None:
                entries: list[CodexHistoryEntry] = []
                load_error = ""
                try:
                    entries = load_codex_session_history(get_codex_home(), session_id, rollout_path)
                except Exception as exc:  # noqa: BLE001
                    load_error = str(exc)
                try:
                    self.call_from_thread(
                        self.post_message,
                        ResumeHistoryLoaded(request_id, session_id, entries, load_error),
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
                self._reset_conversation_detail()
                self.run_info_rows = self._base_info_rows()
                self.status = "Resume cleared"
                self._sync()
                return
            self.resume_entries = list_resume_sessions(
                self.workspace,
                include_non_interactive=bool(getattr(self.args, "resume_include_non_interactive", True)),
            )
            if not args or args[0].lower() == "list":
                self._show_resume_list()
                return
            selector = "1" if args[0].lower() == "latest" else args[0]
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
                    error = subagent_resume_error(get_codex_home(), selector)
                    if error:
                        self.status = error
                        self._sync()
                        return
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

        def _start_run(self, prompt: str) -> None:
            if self.running:
                self.status = "A run is already active"
                self._sync()
                return
            if self._has_pending_run() and (getattr(self.args, "no_sync_back", False) or self.best_agent is None):
                if not self._discard_pending_run():
                    self._sync()
                    return
            if self._has_pending_run() and not self._finalize_agent(self.selected_agent, archive_detail=True):
                self._sync()
                return
            self.running = True
            self.exit_after_run = False
            self.cancel_event = threading.Event()
            self.best_agent = None
            self.started_at = time.monotonic()
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
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
            run_args.no_sync_back = True
            run_args.keep_workspaces = True
            run_args.cancel_event = self.cancel_event

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

            threading.Thread(target=target, name="pcr-tui-runner", daemon=False).start()

        def _post_progress(self, payload: dict[str, Any]) -> None:
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
                self._clear_pending_run()
                self.run_info_rows = self._base_info_rows()
                self.status = f"Continuing from AGENT-{idx:03d}"
                return True
            except Exception as exc:  # noqa: BLE001
                self.status = f"Cannot continue AGENT-{idx:03d}: {exc}"
                return False

        def _show_text(self, text: str) -> None:
            pane = self.agents.setdefault(self.selected_agent, AgentPane(self.selected_agent))
            pane.command_text = text
            self._mark_detail_dirty(pane)
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
                self.status = f"Working {int(time.monotonic() - self.started_at)}s {self._pulse()}"
                self._sync()

        def _sync(self) -> None:
            self.query_one("#tree", Static).update(self._tree_renderable())
            frame = self.query_one("#detail-frame", Vertical)
            pane = self.agents.get(self.selected_agent)
            detail_visible = self._has_detail_content(pane)
            frame.display = detail_visible
            border_style = "green" if pane is not None and pane.idx == self.best_agent else "cyan"
            frame.styles.border = ("round", border_style)
            frame.border_title = self._detail_title(pane) if detail_visible and pane is not None else ""

            scroll = self.query_one("#detail-scroll", VerticalScroll)
            should_follow_end = scroll.is_vertical_scroll_end
            detail = self.query_one("#detail", Static)
            cache_key_before = self._detail_cache_key
            detail_renderable = self._detail_renderable()
            detail_changed = self._detail_cache_key != cache_key_before
            if detail_changed:
                detail.update(detail_renderable)
            if detail_visible and detail_changed and should_follow_end:
                self.call_after_refresh(scroll.scroll_end, animate=False, immediate=True)
            self.query_one("#state", Static).update(self._state_text())

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
            hidden = {"METADATA", "WORKSPACE COPIES"}
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
                ("MODULE_DIR", absolute_path_for_display(module_dir)),
                ("RUN_ANCHOR", absolute_path_for_display(run_anchor)),
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
            if pane.command_text:
                return self._format_prefixed_block(">", pane.command_text)
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
            if pane.command_text:
                renderable: object = Text(self._format_prefixed_block(">", pane.command_text), style="yellow")
                self._detail_cache_key = key
                self._detail_cache_renderable = renderable
                return renderable

            table = Table.grid()
            table.add_column()
            blocks = self._detail_blocks(pane)
            if not blocks:
                self._detail_cache_key = key
                self._detail_cache_renderable = ""
                return ""
            for idx, (prefix, block, style) in enumerate(blocks):
                if idx:
                    table.add_row("")
                table.add_row(Text(self._format_prefixed_block(prefix, block), style=style))
            self._detail_cache_key = key
            self._detail_cache_renderable = table
            return table

        def _archive_agent_detail(self, pane: AgentPane) -> None:
            self.detail_history.extend(self._pane_detail_blocks(pane))
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
            return [*self.detail_history, *self._pane_detail_blocks(pane)]

        def _has_detail_content(self, pane: AgentPane | None) -> bool:
            return pane is not None and (bool(pane.command_text) or bool(self._detail_blocks(pane)))

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
            parts.extend(["Ctrl-C clear/exit", "←/→ switch"])
            return " | ".join(parts)

        def _update_suggestions(self, value: str) -> None:
            suggestions = command_suggestions(value)
            self.suggestion_line_count = len(suggestions)
            widget = self.query_one("#suggestions", Static)
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
        app.run()
        return 0
