from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEXTUAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "show commands"),
    ("/status", "show current run state"),
    ("/numofagents", "show current agent count"),
    ("/numofagents 8", "set agent count"),
    ("/resume", "show resumable Codex sessions"),
    ("/resume 1", "load a listed session"),
    ("/resume latest", "load latest session"),
    ("/resume clear", "start without resume"),
    ("/clear", "clear the current view"),
    ("/exit", "quit"),
)


def command_suggestions(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text.startswith("/"):
        return []
    return [
        f"{command}  {description}"
        for command, description in TEXTUAL_COMMANDS
        if command.startswith(text)
    ][:6]


def compact_text(text: str, limit: int = 600) -> str:
    return text.strip()


def compact_block(text: str, limit: int = 2400) -> str:
    return text.strip()


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
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.containers import Vertical, VerticalScroll
    from textual.message import Message
    from textual.widgets import Static, TextArea
except ModuleNotFoundError as exc:
    _TEXTUAL_IMPORT_ERROR = exc

    def run_textual_tui(_args: argparse.Namespace, _exc: ModuleNotFoundError = _TEXTUAL_IMPORT_ERROR) -> int:
        raise SystemExit("交互式 TUI 需要 textual：请运行 python3 -m pip install -e . 重新安装本项目依赖。") from _exc

else:
    from .app import get_codex_home, infer_codex_thread_id_for_result, list_resume_sessions, promote_best_codex_session_to_workspace, run_once
    from .models import AgentResult
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

    HELP_TEXT = """
Commands:
  /help                 show this help
  /status               show current run state
  /numofagents          show current agent count
  /numofagents <n>      set agent count for the next run
  /resume               show recent sessions
  /resume <n|session>   load a listed session or explicit session id
  /resume latest        load latest Codex session
  /resume clear         start next run without resume
  /clear                clear the current view
  /exit                 quit

Enter any non-command text to run PCR. Left/right switches agent panes.
Ctrl-C clears a non-empty prompt; Ctrl-C on an empty prompt exits.
""".strip()


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
            max-height: 6;
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
            parts = raw.split()
            name = parts[0].lower()
            args = parts[1:]
            if name in {"/exit", "/quit"}:
                self._request_exit()
                return
            if name == "/help":
                self._show_text(HELP_TEXT)
                return
            if name == "/status":
                self._show_status()
                return
            if name == "/clear":
                self.action_clear_view()
                return
            if name == "/numofagents":
                self._handle_numofagents(args)
                return
            if name == "/resume":
                self._handle_resume(args)
                return
            self.status = f"Unknown command: {name}"
            self._sync()

        def _handle_numofagents(self, args: list[str]) -> None:
            if self.running:
                self.status = "Cannot change agent count while running"
                self._sync()
                return
            if not args:
                self.status = f"numofagents={self.num_agents}"
                self._show_text(self.status)
                self._sync()
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
            if self._has_pending_run():
                if getattr(self.args, "no_sync_back", False) or self.best_agent is None:
                    if not self._discard_pending_run():
                        self._sync()
                        return
                elif not self._finalize_agent(self.selected_agent, archive_detail=True):
                    self._sync()
                    return
            self.num_agents = value
            self.selected_agent = min(self.selected_agent, value)
            self.agents = {idx: AgentPane(idx) for idx in range(1, value + 1)}
            self.best_agent = None
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = f"Next run will use {value} agents"
            self._show_text(self.status)
            self._sync()

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
                self.run_info_rows = self._base_info_rows()
                self.status = "Resume cleared"
                self._sync()
                return
            self.resume_entries = list_resume_sessions(self.workspace, include_non_interactive=True)
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
                    self.resume_session_id = selector
                    self.run_info_rows = self._base_info_rows()
                    self.status = f"Resume session set: {selector}"
                    self._sync()
                    return
            if chosen is None:
                self.status = "No resumable session found"
                self._sync()
                return
            self.resume_session_id = chosen.session_id
            self.run_info_rows = self._base_info_rows()
            self.status = f"Resume session set: {chosen.session_id}"
            self._sync()

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
            widget.styles.height = self.suggestion_line_count

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
