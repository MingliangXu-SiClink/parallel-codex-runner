from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.message import Message
    from textual.widgets import Static, TextArea
except ModuleNotFoundError as exc:
    _TEXTUAL_IMPORT_ERROR = exc

    def run_textual_tui(_args: argparse.Namespace, _exc: ModuleNotFoundError = _TEXTUAL_IMPORT_ERROR) -> int:
        raise SystemExit("交互式 TUI 需要 textual：python3 -m pip install 'parallel-codex-runner[tui]'") from _exc

else:
    from .app import list_resume_sessions, run_once

    HELP_TEXT = """
Commands:
  /help                 show this help
  /numofagents          show current agent count
  /numofagents <n>      set agent count for the next run
  /resume               load latest Codex session for this workspace
  /resume list          show recent sessions
  /resume <n|session>   load a listed session or explicit session id
  /resume clear         start next run without resume
  /clear                clear the current view
  /exit                 quit

Enter any non-command text to run PCR. Left/right switches agent panes.
""".strip()


    def compact_text(text: str, limit: int = 600) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 3)].rstrip() + "..."


    def compact_block(text: str, limit: int = 2400) -> str:
        stripped = text.strip()
        if len(stripped) <= limit:
            return stripped
        return stripped[: max(0, limit - 4)].rstrip() + "\n..."


    def value_at(payload: dict[str, Any], *path: str) -> Any:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current


    def display_line_from_json(payload: dict[str, Any]) -> str:
        kind = str(payload.get("type") or payload.get("event") or "").strip()
        text_paths = [
            ("message",),
            ("text",),
            ("content",),
            ("delta",),
            ("summary",),
            ("payload", "message"),
            ("payload", "text"),
            ("payload", "content"),
            ("payload", "delta"),
            ("payload", "summary"),
        ]
        for path in text_paths:
            value = value_at(payload, *path)
            if isinstance(value, str) and value.strip():
                prefix = f"{kind}: " if kind else ""
                return prefix + compact_text(value)

        session_id = (
            value_at(payload, "thread_id")
            or value_at(payload, "session_id")
            or value_at(payload, "payload", "session_id")
            or value_at(payload, "payload", "id")
        )
        if kind and isinstance(session_id, str) and session_id.strip():
            return f"{kind}: {session_id.strip()}"
        if kind:
            return kind
        return compact_text(json.dumps(payload, ensure_ascii=False))


    def display_line_from_output(text: str) -> str:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return compact_text(text)
        if isinstance(payload, dict):
            return display_line_from_json(payload)
        return compact_text(text)


    @dataclass
    class AgentPane:
        idx: int
        status: str = "idle"
        reasoning_tokens: int | None = None
        lines: list[str] = field(default_factory=list)

        def append(self, text: str) -> None:
            text = text.strip()
            if not text:
                return
            self.lines.append(text)
            del self.lines[:-80]


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
        async def _on_key(self, event: events.Key) -> None:
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                self.post_message(PromptSubmitted(self.text))
                return
            if event.key in {"left", "right"} and not self.text.strip():
                event.stop()
                event.prevent_default()
                self.post_message(AgentSwitchRequested(-1 if event.key == "left" else 1))
                return
            if event.key in {"shift+enter", "ctrl+j"}:
                event.stop()
                event.prevent_default()
                start, end = self.selection
                self.replace("\n", start, end, maintain_selection_offset=False)
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
            color: #8fb3ff;
            text-style: bold;
        }
        .box {
            margin: 0 1;
            padding: 1;
            border: round #3b4656;
            background: #111722;
        }
        #tree {
            height: 12;
        }
        #detail {
            height: 1fr;
        }
        #prompt {
            height: 3;
            min-height: 3;
            max-height: 8;
            margin: 0 1;
            padding: 0 1;
            border: round #4c5f74;
            background: #0d1117;
        }
        #state {
            height: 1;
            margin: 0 1;
            padding: 0 1;
            background: #171d25;
            color: #ffffff;
        }
        """

        BINDINGS = [
            ("ctrl+q", "quit", "Quit"),
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

        def compose(self) -> ComposeResult:
            with Vertical(id="root"):
                yield Static("Agents", classes="caption")
                yield Static("", id="tree", classes="box")
                yield Static("Agent detail", classes="caption")
                yield Static("", id="detail", classes="box")
                yield PromptEditor("", id="prompt", soft_wrap=True, show_line_numbers=False)
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
            if not value:
                return
            if value.startswith("/"):
                self._handle_command(value)
            else:
                self._start_run(value)
            prompt.focus()

        @on(AgentSwitchRequested)
        def _on_switch(self, event: AgentSwitchRequested) -> None:
            self.selected_agent = min(self.num_agents, max(1, self.selected_agent + event.delta))
            self._sync()

        @on(RunnerEvent)
        def _on_runner_event(self, event: RunnerEvent) -> None:
            payload = event.payload
            idx = int(payload.get("idx") or 0)
            pane = self.agents.get(idx)
            kind = str(payload.get("type") or "")
            if kind == "agent_status" and pane is not None:
                pane.status = str(payload.get("status") or pane.status)
            elif kind == "agent_started" and pane is not None:
                pane.status = "running"
            elif kind == "agent_tokens" and pane is not None:
                value = payload.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
            elif kind == "agent_line" and pane is not None:
                pane.status = "running"
                pane.append(display_line_from_output(str(payload.get("text") or "")))
            elif kind == "agent_finished" and pane is not None:
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                pane.status = str(result.get("status") or "finished")
                final_text = self._read_final_message(result)
                pane.append(final_text or str(result.get("stdout_tail") or result.get("stderr_tail") or ""))
            elif kind == "run_finished":
                self.running = False
                self.best_agent = payload.get("best_agent") if isinstance(payload.get("best_agent"), int) else None
                self.status = "Finished" if payload.get("success") else "No successful agent"
            elif kind == "run_failed":
                self.running = False
                self.status = f"Run failed: {payload.get('message') or ''}"
            self._sync()

        def action_clear_view(self) -> None:
            for pane in self.agents.values():
                pane.status = "idle"
                pane.reasoning_tokens = None
                pane.lines.clear()
            self.best_agent = None
            self.status = "Ready"
            self._sync()

        def _handle_command(self, raw: str) -> None:
            parts = raw.split()
            name = parts[0].lower()
            args = parts[1:]
            if name in {"/exit", "/quit"}:
                self.exit()
                return
            if name == "/help":
                self._show_text(HELP_TEXT)
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
            if not args:
                self.status = f"numofagents={self.num_agents}"
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
            self.num_agents = value
            self.selected_agent = min(self.selected_agent, value)
            self.agents = {idx: self.agents.get(idx, AgentPane(idx)) for idx in range(1, value + 1)}
            self.status = f"Next run will use {value} agents"
            self._sync()

        def _handle_resume(self, args: list[str]) -> None:
            if args and args[0].lower() in {"clear", "new"}:
                self.resume_session_id = ""
                self.status = "Resume cleared"
                self._sync()
                return
            self.resume_entries = list_resume_sessions(self.workspace, include_non_interactive=True)
            if args and args[0].lower() == "list":
                lines = ["Recent sessions:"]
                for index, session in enumerate(self.resume_entries[:8], 1):
                    lines.append(f"{index}. {session.session_id}  {session.title[:80]}")
                self._show_text("\n".join(lines) if len(lines) > 1 else "No resumable sessions found")
                return
            selector = args[0] if args else "1"
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
                    self.status = f"Resume session set: {selector}"
                    self._sync()
                    return
            if chosen is None:
                self.status = "No resumable session found"
                self._sync()
                return
            self.resume_session_id = chosen.session_id
            self.status = f"Resume session set: {chosen.session_id}"
            self._sync()

        def _start_run(self, prompt: str) -> None:
            if self.running:
                self.status = "A run is already active"
                self._sync()
                return
            self.running = True
            self.best_agent = None
            self.started_at = time.monotonic()
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.selected_agent = min(self.selected_agent, self.num_agents)
            self.status = "Preparing agents"
            self._sync()

            run_args = argparse.Namespace(**vars(self.args))
            run_args.prompt = prompt
            run_args.prompt_file = None
            run_args.num_agents = self.num_agents
            run_args.max_parallel = min(run_args.max_parallel or self.num_agents, self.num_agents)
            run_args.resume = False
            run_args.resume_session_id = self.resume_session_id or None

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

            threading.Thread(target=target, name="pcr-tui-runner", daemon=True).start()

        def _post_progress(self, payload: dict[str, Any]) -> None:
            self.call_from_thread(self.post_message, RunnerEvent(payload))

        def _show_text(self, text: str) -> None:
            pane = self.agents.setdefault(self.selected_agent, AgentPane(self.selected_agent))
            pane.lines = text.splitlines()
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
                self.status = f"Running {int(time.monotonic() - self.started_at)}s"
                self._sync()

        def _sync(self) -> None:
            self.query_one("#tree", Static).update(self._tree_text())
            self.query_one("#detail", Static).update(self._detail_text())
            self.query_one("#state", Static).update(self._state_text())

        def _tree_text(self) -> str:
            lines = [f"workspace: {self.workspace}", f"agents: {self.num_agents}  resume: {self.resume_session_id or '-'}", ""]
            for idx in range(1, self.num_agents + 1):
                pane = self.agents[idx]
                marker = ">" if idx == self.selected_agent else " "
                best = " *best*" if idx == self.best_agent else ""
                rtok = "-" if pane.reasoning_tokens is None else str(pane.reasoning_tokens)
                lines.append(f"{marker} agent_{idx:03d}  {pane.status:<9}  rtok={rtok}{best}")
            return "\n".join(lines)

        def _detail_text(self) -> str:
            pane = self.agents.get(self.selected_agent)
            if pane is None:
                return ""
            lines = [f"agent_{pane.idx:03d}  status={pane.status}  rtok={pane.reasoning_tokens if pane.reasoning_tokens is not None else '-'}", ""]
            lines.extend(pane.lines[-30:] or ["No output yet."])
            return "\n".join(lines)

        def _state_text(self) -> str:
            return f"{self.status} | left/right: switch agent | /help"


    def run_textual_tui(args: argparse.Namespace) -> int:
        app = PcrTextualApp(args)
        app.run()
        return 0
