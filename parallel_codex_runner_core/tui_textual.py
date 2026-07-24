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
from .codex_models import CodexModelRegistry
from .prompt_history import PromptHistoryNavigator, PromptHistoryStore
from .workspace_config import WorkspaceConfigStore, WorkspaceSettings

TEXTUAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "show all TUI commands"),
    ("/status", "show current run configuration"),
    ("/config", "show current run configuration"),
    ("/accept", "finalize the currently displayed successful agent"),
    ("/reject", "exclude the currently displayed agent from recommendations"),
    ("/retry [agent]", "rerun a failed or killed agent on a fresh workspace"),
    ("/more <n>", "add more candidates for the current question"),
    ("/diff", "toggle the full workspace diff for the current agent"),
    ("/kill [agent]", "stop a running agent while queued agents continue normally"),
    ("/numofagents <n>", "set the number of agents for the next run"),
    ("/maxparallel <n|auto>", "limit how many agents may run concurrently"),
    ("/serial", "run agents one at a time"),
    ("/parallel", "run agents concurrently"),
    (
        "/recommendby <duration|reasoning_tokens>",
        "choose how successful agents are recommended",
    ),
    (
        "/synthesis <n|off>",
        "set isolated review-and-synthesis agents for the next run",
    ),
    ("/subagents <on|off>", "allow or block nested Codex agents for the next run"),
    ("/subagentslimit <n>", "limit nested Codex agents within each PCR agent"),
    ("/model <name|clear>", "set or clear the Codex model for the next run"),
    ("/effort <auto|level>", "set a model-supported reasoning effort for the next run"),
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
MAX_SUGGESTIONS = 15
UNKNOWN_RESUME_MODEL = "__pcr_unknown_resume_model__"
TIP_ROTATION_SECONDS = 10.0
TIP_ICON_REFRESH_SECONDS = 0.1
FOLLOW_UP_DELAY_SECONDS = 60.0
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
RECOMMEND_BORDER_REFRESH_SECONDS = 0.18
RECOMMEND_BORDER_COLORS: tuple[str, ...] = (
    "#ff5f6d",
    "#ff8a4c",
    "#ffd166",
    "#67d17c",
    "#2dd4bf",
    "#38bdf8",
    "#5b8def",
    "#8b5cf6",
    "#d946ef",
    "#ff5fa2",
)
RECOMMEND_TITLE_BACKGROUND = "#123326"
COMMAND_SPINNER_FRAMES: tuple[str, ...] = (
    "⠋",
    "⠙",
    "⠹",
    "⠸",
    "⠼",
    "⠴",
    "⠦",
    "⠧",
    "⠇",
    "⠏",
)
TUI_TIPS: tuple[str, ...] = (
    "输入 / 可查看并补全命令。",
    "输入框为空时，←/→ 可切换 Agent。",
    "Shift-Enter 可在输入框中换行。",
    "选中Agent栏目文本即可复制。",
    "使用 /resume 可载入之前的 Codex 对话。",
    "后续提问会从当前显示的已完成 Agent 继续。",
    "某个 Agent 完成后，即可从该 Agent 继续提问。",
    "AGENTS、MODEL 等运行配置只作用于下一轮，不会选择当前 Agent。",
    "PCR 会按 workspace 记住顶部配置，下次打开同一目录时自动恢复。",
    "EFFORT 会随 MODEL 更新可选等级，auto 会选择兼容的推理等级。",
    "带 ★ 和彩虹边框的 Agent 是当前推荐结果。",
    "退出或切换 WORKSPACE、RESUME 时，会采用当前显示的 Agent。",
    "输入 /accept 可立即采用当前 Agent。",
    "输入 /reject 可将当前 Agent 排除在推荐范围外。",
    "输入 /retry 可重跑失败或被终止的 Agent，/more 3 可追加 3 个候选 Agent。",
    "使用 /synthesis 3 可在候选完成后运行 3 个独立综合 Agent。",
    "综合 Agent 会审核全部成功候选；你仍可采用任意成功 Agent。",
    "输入 /diff 可切换当前 Agent 的完整文件差异。",
    "TUI 不在前台时，所有 Agent 结束后会触发完成通知，失败也算结束。",
    "Ctrl-C 会依情境执行复制、清空输入或退出。",
    "KEEP_WORKSPACES 可保留候选工作目录。",
    "SYNC_BACK 控制是否同步选中的结果至工作区。",
    "注意本项目默认以Codex Full Access 权限运行。",
    "运行中输入 /kill，可终止当前显示且正在运行的 Agent，排队 Agent 会正常加入队列。",
    "输入框中按 ↑/↓ 可浏览当前 Workspace 与 Session 的输入历史。",
    "大型工作区会在复制前估算空间，并在预计超过 5 GiB 时请求确认。",
    "SUBAGENTS 默认关闭；启用后可用 SUBAGENTS_LIMIT 控制每个 PCR Agent 的嵌套数量。",
    "当前 Agent 未完成时提交的新问题会排队，本轮结束后留出 60 秒选择续接的 Agent。",
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
            "Prompts submitted from unfinished agents queue without stopping the active run.",
            "Run-setting commands apply to the next run without selecting an agent.",
            "Accept, follow-up, exit, workspace, and resume actions use the displayed agent.",
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


def normalize_reasoning_token_counts(value: Any) -> dict[int, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[int, int] = {}
    for raw_delta, raw_count in value.items():
        try:
            delta = int(raw_delta)
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if delta > 0 and count > 0:
            normalized[delta] = normalized.get(delta, 0) + count
    return normalized


def format_reasoning_tokens_title(
    value: int | None,
    counts: Any,
    completed: bool,
) -> str:
    normalized = normalize_reasoning_token_counts(counts)
    if not normalized:
        return "" if value is None else f"reasoning_tokens={value}"

    # Codex's cumulative value includes resumed history, while these increments
    # describe this run. Derive the title total from the same increment set.
    run_total = sum(delta * count for delta, count in normalized.items())
    label = f"reasoning_tokens={run_total}"
    ranked = sorted(
        normalized.items(),
        key=lambda item: (-(item[0] * item[1]), -item[0]),
    )
    visible = ranked[:4]
    other_count = sum(count for _delta, count in ranked[4:])
    if not completed:
        values = [f"{delta}:{count}" for delta, count in visible]
        if other_count:
            values.append(f"other:{other_count}")
        return f"{label}({', '.join(values)})"

    total = sum(normalized.values())
    values = []
    for delta, count in visible:
        percentage = count * 100 / total
        percentage_text = (
            str(int(percentage))
            if percentage.is_integer()
            else f"{percentage:.1f}".rstrip("0").rstrip(".")
        )
        values.append(f"{delta}:{percentage_text}%")
    if other_count:
        percentage = other_count * 100 / total
        percentage_text = (
            str(int(percentage))
            if percentage.is_integer()
            else f"{percentage:.1f}".rstrip("0").rstrip(".")
        )
        values.append(f"other:{percentage_text}%")
    values.append(f"total:{total}")
    return f"{label}({', '.join(values)})"


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


def split_command_detail(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    completion = ""
    if len(lines) > 1 and lines[-1].startswith(("✓ ", "✗ ", "× ", "· ")):
        completion = lines.pop()
    return "\n".join(lines).strip(), completion


def strip_shell_command_wrapper(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if (
        len(parts) == 3
        and Path(parts[0]).name in {"bash", "dash", "ksh", "sh", "zsh"}
        and parts[1] in {"-c", "-lc"}
    ):
        return parts[2]
    return command


def command_completion_display(completion: str) -> tuple[str, str]:
    if not completion:
        return "", "running"
    if completion.startswith("✓ exit "):
        return f"completed (exit {completion.removeprefix('✓ exit ')})", "success"
    if completion.startswith("✗ exit "):
        return f"failed (exit {completion.removeprefix('✗ exit ')})", "failed"
    if completion.startswith("✓"):
        return "completed", "success"
    if completion.startswith("✗"):
        return "failed", "failed"
    if completion.startswith("×"):
        return "cancelled", "cancelled"
    return completion.removeprefix("· "), "other"


def command_detail_display(text: str, *, active: bool = True) -> tuple[str, str]:
    command, completion = split_command_detail(text)
    command = strip_shell_command_wrapper(command) or "command"
    completion_text, state = command_completion_display(completion)
    if not completion and not active:
        completion_text = "interrupted"
        state = "cancelled"

    command_lines = command.splitlines() or ["command"]
    verb = "Running" if state == "running" else "Ran"
    lines = [f"{verb} {command_lines[0]}"]
    lines.extend(f"│ {line}" for line in command_lines[1:])
    if completion_text:
        lines.append(f"└ {completion_text}")
    return "\n".join(lines), state


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
        header = command or "command"
        completion = command_completion_line(status, exit_code)
        return "command", f"{header}\n{completion}" if completion else header

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
    from textual.color import Color
    from textual.content import Content
    from textual.containers import Grid, Vertical, VerticalScroll
    from textual.geometry import Region
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Select, Static, TextArea
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
        LARGE_RUN_STORAGE_WARNING_BYTES,
        RunStorageEstimate,
        available_storage_bytes,
        estimate_staged_run_storage,
        format_storage_bytes,
        get_codex_home,
        infer_codex_thread_id_for_result,
        list_resume_sessions,
        load_codex_session_history,
        normalize_recommend_by,
        promote_best_codex_session_to_workspace,
        remove_agent_codex_homes,
        run_additional_agents,
        run_once,
        select_best_result,
        subagent_resume_error,
    )
    from .diffing import build_workspace_diff_text
    from .models import (
        AGENT_ROLE_CANDIDATE,
        AGENT_ROLE_SYNTHESIS,
        DEFAULT_SUBAGENTS_LIMIT,
        AgentResult,
        CodexHistoryEntry,
        normalize_agent_role,
    )
    from .paths import absolute_path_for_display, choose_run_base, default_run_anchor, is_relative_to
    from .workspace import (
        cleanup_workspace_copies,
        estimate_path_storage_bytes,
        sync_best_workspace_back,
    )
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
        return CodexModelRegistry.load(get_codex_home()).model_options(current_model)


    def codex_effort_options(
        model: str | None,
        current_effort: str | None,
    ) -> list[tuple[str, str]]:
        return CodexModelRegistry.load(get_codex_home()).effort_options(
            model,
            current_effort,
        )


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
        role: str = AGENT_ROLE_CANDIDATE
        status: str = "idle"
        rejected: bool = False
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


    @dataclass(frozen=True)
    class QueuedFollowUp:
        prompt: str
        record_history: bool = False


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
            self.value = value


    class PromptHistoryRequested(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction


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
            max-height: 1;
            border: none;
            padding: 0;
            background: #171d25;
        }
        .runner-control > SelectCurrent,
        .runner-control > SelectCurrent > #label {
            height: 1;
            min-height: 1;
            max-height: 1;
            text-wrap: nowrap;
            text-overflow: clip;
        }
        .runner-control:focus {
            background: #243448;
            color: #ffffff;
        }
        #config-agents, #config-synthesis-agents, #config-max-parallel,
        #config-subagents-limit {
            width: 12;
        }
        #config-execution, #config-recommend-by, #config-effort {
            width: 24;
        }
        #config-model {
            width: 36;
        }
        #config-subagents, #config-sync-back, #config-keep-workspaces {
            width: 10;
        }
        #detail-frame {
            height: 1fr;
            margin: 0 1;
            border: round cyan;
            border-title-align: left;
            border-title-color: #c8edf5;
            border-title-background: #101216;
            border-title-style: bold;
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
        #follow-up-queue-frame {
            height: 1;
            min-height: 1;
            max-height: 6;
            margin: 0 1;
            background: #141b23;
            color: #d6e4f0;
            scrollbar-size: 1 1;
        }
        #follow-up-queue {
            width: 100%;
            height: auto;
            padding: 0 1;
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
            self.workspace_config_store = WorkspaceConfigStore()
            self._workspace_config_explicit = frozenset(
                getattr(args, "_pcr_explicit_tui_settings", set())
            )
            self._apply_saved_workspace_settings()
            self.num_agents = args.num_agents
            self.synthesis_agents = max(
                0,
                int(getattr(args, "synthesis_agents", 0) or 0),
            )
            self.args.synthesis_agents = self.synthesis_agents
            self.resume_session_id = (args.resume_session_id or "").strip()
            self.resume_entries = []
            self.model_registry = CodexModelRegistry.load(get_codex_home())
            resume_model_pending = bool(
                (getattr(args, "resume", False) or self.resume_session_id)
                and not getattr(args, "model", None)
            )
            if not resume_model_pending and not self.model_registry.effort_is_supported(
                getattr(args, "model", None), getattr(args, "effort", None)
            ):
                self.args.effort = None
            self.model_choices = self.model_registry.model_options(
                getattr(args, "model", None)
            )
            self.effort_choices = self.model_registry.effort_options(
                UNKNOWN_RESUME_MODEL
                if resume_model_pending
                else getattr(args, "model", None),
                getattr(args, "effort", None),
            )
            self.resume_choices = resume_select_options([], self.resume_session_id)
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.selected_agent = 1
            self.running = False
            self.status = "Ready"
            self.recommended_agent: int | None = None
            self.started_at: float | None = None
            self.prompt_height = 3
            self.suggestion_line_count = 0
            self.tip_index = 0
            self.tip_icon_index = 0
            self.recommend_border_frame = 0
            self.recommend_border_edge_index = 0
            self.work_frame = 0
            self.exit_after_run = False
            self.cancel_event: threading.Event | None = None
            self.agent_cancel_events: dict[int, threading.Event] = {}
            self.runner_thread: threading.Thread | None = None
            self._shutdown_cleanup_started = False
            self.run_info_rows = self._base_info_rows()
            self.pending_run_root: Path | None = None
            self.pending_workspaces_root: Path | None = None
            self.pending_workspace: Path | None = None
            self.pending_no_sync_back: bool | None = None
            self.pending_keep_workspaces: bool | None = None
            self.pending_prompt = ""
            self.pending_prompt_records_history = False
            self.pending_execution_args: argparse.Namespace | None = None
            self.candidate_batches: list[CandidateBatch] = []
            self.active_batch_indices: set[int] = set()
            self.pending_accept_agent: int | None = None
            self.detail_history: list[tuple[str, str, str]] = []
            self.command_history: list[tuple[str, str, str]] = []
            self.prompt_history_store = PromptHistoryStore()
            self.prompt_history_context = self.prompt_history_store.context_key(
                self.workspace,
                self.resume_session_id,
            )
            self.prompt_history_drafts: dict[tuple[str, str], str] = {
                self.prompt_history_context: ""
            }
            self.prompt_history_navigator = PromptHistoryNavigator(
                self.prompt_history_store.entries(*self.prompt_history_context)
            )
            self._prompt_history_programmatic_values: list[str] = []
            self.detail_revision = 0
            self.resume_history_request = 0
            self.resume_choices_request = 0
            self.resume_choices_loaded = False
            self.resume_choices_inflight: tuple[int, Path] | None = None
            self.pending_resume_selector: str | None = None
            self._resume_io_lock = threading.Lock()
            self.storage_preflight_request = 0
            self.storage_preflight_inflight = False
            self.queued_prompt = ""
            self.queued_agent: int | None = None
            self.queued_prompt_records_history = False
            # Follow-ups submitted from unfinished panes wait without stopping
            # the current run; queued_prompt above remains the immediate path.
            self.follow_up_queue: list[QueuedFollowUp] = []
            self.follow_up_continue_at: float | None = None
            self.follow_up_ready = False
            self.follow_up_source_finalized = False
            self._follow_up_countdown_second: int | None = None
            self._follow_up_queue_cache = ""
            self._follow_up_queue_items_cache: tuple[str, ...] = ()
            self._follow_up_queue_refresh_deferred = False
            self._updating_controls = False
            self._latest_select_event_time: dict[str, float] = {}
            self._committed_model_effort_values: dict[str, str] = {
                "config-model": str(getattr(self.args, "model", None) or ""),
                "config-effort": str(getattr(self.args, "effort", None) or ""),
            }
            self._committed_input_values = {
                "config-agents": str(self.num_agents),
                "config-synthesis-agents": str(self.synthesis_agents),
                "config-max-parallel": dict(self.run_info_rows).get("MAX_PARALLEL", ""),
                "config-subagents-limit": str(
                    getattr(
                        self.args,
                        "subagents_limit",
                        DEFAULT_SUBAGENTS_LIMIT,
                    )
                ),
            }
            self._mouse_down_in_runner_control = False
            self._last_screen_selection = ""
            self._sync_deferred_for_selection = False
            self._tip_refresh_deferred_for_selection = False
            self._recommend_border_deferred_for_selection = False
            self._detail_cache_key: tuple[Any, ...] | None = None
            self._detail_cache_renderable: object = ""
            self.app_in_foreground = True
            self.completion_notification_sent = False

        def _apply_saved_workspace_settings(self) -> bool:
            settings = self.workspace_config_store.load(self.workspace)
            if settings is None:
                return False
            settings.apply_to_args(self.args, self._workspace_config_explicit)
            return True

        def _persist_workspace_settings(self) -> bool:
            settings = WorkspaceSettings.from_runtime(
                self.num_agents,
                self.synthesis_agents,
                self.args,
                self.resume_session_id,
            )
            return self.workspace_config_store.save(self.workspace, settings)

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

        def _screen_selection_active(self) -> bool:
            try:
                if not self.screen.selections:
                    return False
                return bool(self.screen.get_selected_text())
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
            refresh_follow_up_queue = self._follow_up_queue_refresh_deferred
            sync = self._sync_deferred_for_selection
            refresh_recommend_border = (
                self._recommend_border_deferred_for_selection
                and not self._screen_selection_active()
            )
            self._tip_refresh_deferred_for_selection = False
            self._follow_up_queue_refresh_deferred = False
            self._sync_deferred_for_selection = False
            if refresh_recommend_border:
                self._recommend_border_deferred_for_selection = False
            if refresh_tip:
                self._refresh_tip()
            if refresh_follow_up_queue:
                self._refresh_follow_up_queue()
            if sync:
                self._sync()

        async def on_event(self, event: events.Event) -> None:
            if isinstance(event, events.AppBlur):
                self.app_in_foreground = False
                if self.mouse_captured is not None:
                    self.capture_mouse(None)
            elif isinstance(event, events.AppFocus):
                self.app_in_foreground = True

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
                        yield Static("CODEX_BIN", classes="runner-key")
                        yield Static("", id="runner-codex-bin", classes="runner-value", markup=False)
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
                        yield Static("SYNTHESIS_AGENTS", classes="runner-key")
                        yield Input(
                            str(self.synthesis_agents),
                            id="config-synthesis-agents",
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
                        yield Static("SUBAGENTS", classes="runner-key")
                        yield Select(
                            [("YES", True), ("NO", False)],
                            value=bool(getattr(self.args, "subagents", False)),
                            allow_blank=False,
                            compact=True,
                            id="config-subagents",
                            classes="runner-control",
                        )
                        yield Static("SUBAGENTS_LIMIT", classes="runner-key")
                        yield Input(
                            str(
                                getattr(
                                    self.args,
                                    "subagents_limit",
                                    DEFAULT_SUBAGENTS_LIMIT,
                                )
                            ),
                            id="config-subagents-limit",
                            classes="runner-control",
                            type="integer",
                        )
                        yield Static("RECOMMEND_BY", classes="runner-key")
                        yield Select(
                            [("reasoning_tokens", "reasoning_tokens"), ("duration", "duration")],
                            value=str(
                                getattr(self.args, "recommend_by", "reasoning_tokens")
                            ),
                            allow_blank=False,
                            compact=True,
                            id="config-recommend-by",
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
                        yield Static("EFFORT", classes="runner-key")
                        yield Select(
                            self.effort_choices,
                            value=str(getattr(self.args, "effort", None) or ""),
                            allow_blank=False,
                            compact=True,
                            id="config-effort",
                            classes="runner-control",
                        )
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
                        yield Static("RECOMMENDED AGENT", id="runner-recommended-agent-key", classes="runner-key")
                        yield Static("", id="runner-recommended-agent", classes="runner-value", markup=False)
                with RainbowDetailFrame(id="detail-frame"):
                    with DetailScroll(id="detail-scroll"):
                        yield DetailView("", id="detail")
                yield Static("", id="suggestions")
                yield Static(self._tip_renderable(), id="tips")
                with VerticalScroll(id="follow-up-queue-frame"):
                    yield Static("", id="follow-up-queue", markup=False)
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
            self.set_interval(
                RECOMMEND_BORDER_REFRESH_SECONDS,
                self._advance_recommend_border,
            )
            if getattr(self.args, "resume", False) and not self.resume_session_id:
                self._handle_resume([])
            elif self.resume_session_id:
                self._select_resume_session(self.resume_session_id)
            else:
                self._sync()
            # Restoring the selected session must not suppress discovery of
            # other sessions in the picker.
            if not self.is_headless:
                self._refresh_resume_control()
            self.query_one("#prompt", PromptEditor).focus()

        def _current_prompt_history_context(self) -> tuple[str, str]:
            return self.prompt_history_store.context_key(
                self.workspace,
                self.resume_session_id,
            )

        def _save_prompt_history_draft(self) -> None:
            self.prompt_history_drafts[self.prompt_history_context] = (
                self.prompt_history_navigator.draft
            )

        def _replace_prompt_text(self, text: str) -> None:
            try:
                prompt = self.query_one("#prompt", PromptEditor)
            except Exception:
                return
            if prompt.text != text:
                self._prompt_history_programmatic_values.append(text)
                prompt.text = text
            lines = text.split("\n")
            prompt.move_cursor((len(lines) - 1, len(lines[-1])))
            self._update_suggestions(text)
            self._sync_prompt_height()
            prompt.focus()

        def _clear_prompt_draft(self) -> None:
            self.prompt_history_navigator.note_edit("")
            self.prompt_history_drafts[self.prompt_history_context] = ""
            self._replace_prompt_text("")

        def _load_prompt_history_context(self, draft: str | None = None) -> None:
            self._save_prompt_history_draft()
            context = self._current_prompt_history_context()
            if draft is None:
                draft = self.prompt_history_drafts.get(context, "")
            self.prompt_history_context = context
            self.prompt_history_drafts[context] = draft
            self.prompt_history_navigator.reset(
                self.prompt_history_store.entries(*context),
                draft,
            )
            self._replace_prompt_text(draft)

        def _storage_estimate_inputs(self) -> tuple[Path, str | None, Path | None]:
            source_workspace = self.workspace
            resume_session_id = self.resume_session_id or None
            resume_codex_home: Path | None = None
            if self._has_pending_run() and not self._pending_sync_disabled():
                pane = self.agents.get(self.selected_agent)
                result = pane.result if pane is not None else None
                if isinstance(result, dict) and result.get("status") == "success":
                    workspace_dir = result.get("workspace_dir")
                    if isinstance(workspace_dir, str) and workspace_dir:
                        source_workspace = Path(workspace_dir).expanduser().resolve()
                    thread_id = result.get("codex_thread_id")
                    if isinstance(thread_id, str) and thread_id.strip():
                        resume_session_id = thread_id.strip()
                    codex_home = result.get("codex_home")
                    if isinstance(codex_home, str) and codex_home:
                        resume_codex_home = Path(codex_home).expanduser().resolve()
            return source_workspace, resume_session_id, resume_codex_home

        def _storage_run_base(self) -> Path:
            module_dir = Path(__file__).resolve().parent
            run_anchor = default_run_anchor(module_dir, self.workspace)
            return choose_run_base(
                run_anchor,
                self.workspace,
                getattr(self.args, "runs_dir", None),
            )

        def _storage_preflight_configuration(self) -> tuple[Any, ...]:
            pane = self.agents.get(self.selected_agent)
            result = pane.result if pane is not None else None
            result_values = (
                (
                    result.get("workspace_dir"),
                    result.get("codex_home"),
                    result.get("codex_thread_id"),
                )
                if isinstance(result, dict)
                else (None, None, None)
            )
            return (
                str(self.workspace),
                self.num_agents,
                self.synthesis_agents,
                self.resume_session_id,
                str(getattr(self.args, "runs_dir", None) or ""),
                self.selected_agent,
                str(self.pending_run_root or ""),
                self._pending_sync_disabled(),
                self._pending_keep_enabled(),
                *result_values,
            )

        def _restore_prompt_after_storage_failure(self, prompt_text: str) -> None:
            try:
                prompt = self.query_one("#prompt", PromptEditor)
            except Exception:
                return
            if prompt.text:
                return
            self.prompt_history_drafts[self.prompt_history_context] = prompt_text
            self.prompt_history_navigator.note_edit(prompt_text)
            self._replace_prompt_text(prompt_text)

        def _request_run_with_storage_check(
            self,
            prompt: str,
            record_history: bool = False,
            *,
            from_follow_up_queue: bool = False,
            bypass_follow_up_queue: bool = False,
        ) -> bool:
            if (
                self.follow_up_queue
                and not from_follow_up_queue
                and not bypass_follow_up_queue
                and not self.running
            ):
                return self._enqueue_follow_up(
                    prompt,
                    record_history=record_history,
                )
            if self.running:
                return self._start_run(prompt, record_history=record_history)
            if self.storage_preflight_inflight:
                self.status = "A storage check is already in progress"
                self._sync()
                return False
            if not self._commit_runner_inputs():
                self._sync()
                return False

            source_workspace, resume_session_id, resume_codex_home = (
                self._storage_estimate_inputs()
            )
            try:
                run_base = self._storage_run_base()
            except Exception as exc:  # noqa: BLE001
                self.status = f"Run failed: cannot resolve run storage: {exc}"
                self._sync()
                return False

            reclaimable_root: Path | None = None
            if (
                self._has_pending_run()
                and not self._pending_keep_enabled()
                and self.pending_run_root is not None
                and self.pending_workspaces_root is not None
                and self.pending_run_root.parent.resolve() == run_base.resolve()
            ):
                reclaimable_root = self.pending_workspaces_root

            self.storage_preflight_request += 1
            request_id = self.storage_preflight_request
            self.storage_preflight_inflight = True
            candidate_agents = self.num_agents
            synthesis_agents = self.synthesis_agents
            codex_home = get_codex_home()
            configuration_key = self._storage_preflight_configuration()
            self.status = (
                f"Estimating storage for {candidate_agents} candidates"
                + (
                    f" + {synthesis_agents} synthesis agents"
                    if synthesis_agents
                    else ""
                )
            )
            self._sync()

            def target() -> None:
                estimate: RunStorageEstimate | None = None
                error = ""
                reclaimable_bytes = 0
                try:
                    estimate = estimate_staged_run_storage(
                        source_workspace,
                        candidate_agents,
                        synthesis_agents,
                        resume_session_id=resume_session_id,
                        codex_home=codex_home,
                        resume_codex_home=resume_codex_home,
                    )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                if not error and reclaimable_root is not None:
                    try:
                        reclaimable_bytes = estimate_path_storage_bytes(
                            reclaimable_root
                        )
                    except OSError:
                        reclaimable_bytes = 0
                try:
                    self.call_from_thread(
                        self.post_message,
                        StoragePreflightFinished(
                            request_id,
                            prompt,
                            record_history,
                            run_base,
                            estimate,
                            error,
                            reclaimable_bytes,
                            configuration_key,
                            from_follow_up_queue,
                            bypass_follow_up_queue,
                        ),
                    )
                except RuntimeError:
                    pass

            threading.Thread(
                target=target,
                name="pcr-storage-preflight",
                daemon=True,
            ).start()
            return True

        def _restart_changed_storage_preflight(
            self,
            event: StoragePreflightFinished,
        ) -> bool:
            if (
                event.configuration_key
                and event.configuration_key != self._storage_preflight_configuration()
            ):
                self.storage_preflight_inflight = False
                if not self._request_run_with_storage_check(
                    event.prompt,
                    record_history=event.record_history,
                    from_follow_up_queue=event.from_follow_up_queue,
                    bypass_follow_up_queue=event.bypass_follow_up_queue,
                ):
                    if not event.from_follow_up_queue:
                        self._restore_prompt_after_storage_failure(event.prompt)
                return True
            return False

        def _complete_storage_preflight(
            self,
            event: StoragePreflightFinished,
            continue_run: bool,
        ) -> None:
            if event.request_id != self.storage_preflight_request:
                return
            if not continue_run:
                self.storage_preflight_inflight = False
                if event.from_follow_up_queue:
                    self._consume_follow_up(event.prompt)
                self.status = "Prompt ignored: storage confirmation declined"
                self._sync()
                self.query_one("#prompt", PromptEditor).focus()
                return
            if self._restart_changed_storage_preflight(event):
                return

            estimate = event.estimate
            if estimate is None:
                self.storage_preflight_inflight = False
                self.status = "Run failed: storage estimate is unavailable"
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_failure(event.prompt)
                self._sync()
                return
            try:
                available = available_storage_bytes(event.run_base)
            except Exception as exc:  # noqa: BLE001
                self.storage_preflight_inflight = False
                self.status = f"Run failed: cannot inspect available disk space: {exc}"
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_failure(event.prompt)
                self._sync()
                return
            usable = available + event.reclaimable_bytes
            if estimate.total_bytes > usable:
                self.storage_preflight_inflight = False
                available_text = format_storage_bytes(available)
                if event.reclaimable_bytes:
                    available_text += (
                        f" + {format_storage_bytes(event.reclaimable_bytes)} "
                        "reclaimable"
                    )
                self.status = (
                    "Run failed: insufficient disk space "
                    f"(need {format_storage_bytes(estimate.total_bytes)}, "
                    f"available {available_text})"
                )
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_failure(event.prompt)
                self._sync()
                return

            self.storage_preflight_inflight = False
            started = self._start_run(
                event.prompt,
                record_history=event.record_history,
            )
            if started and event.from_follow_up_queue:
                self._consume_follow_up(event.prompt)
            elif not started and not event.from_follow_up_queue:
                self._restore_prompt_after_storage_failure(event.prompt)
                self._sync()

        @on(StoragePreflightFinished)
        def _on_storage_preflight_finished(
            self,
            event: StoragePreflightFinished,
        ) -> None:
            if event.request_id != self.storage_preflight_request:
                return
            if self._restart_changed_storage_preflight(event):
                return
            if event.error or event.estimate is None:
                self.storage_preflight_inflight = False
                message = event.error or "storage estimate is unavailable"
                self.status = f"Run failed: cannot estimate storage: {message}"
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_failure(event.prompt)
                self._sync()
                return
            if event.estimate.total_bytes > LARGE_RUN_STORAGE_WARNING_BYTES:
                self.status = (
                    "Storage confirmation required: "
                    f"{format_storage_bytes(event.estimate.total_bytes)} estimated"
                )
                self._sync()
                self.push_screen(
                    StorageWarningScreen(event.estimate),
                    lambda confirmed: self._complete_storage_preflight(
                        event,
                        bool(confirmed),
                    ),
                )
                return
            self._complete_storage_preflight(event, True)

        def _submit_task_prompt(self, prompt: str) -> bool:
            actual_context = self._current_prompt_history_context()
            if actual_context != self.prompt_history_context:
                self._load_prompt_history_context()
            submitted_from_context = self.prompt_history_context
            if not self._request_run_with_storage_check(prompt, record_history=True):
                return False
            self.prompt_history_drafts[submitted_from_context] = ""
            self._load_prompt_history_context(draft="")
            return True

        def _record_started_prompt(self, prompt: str) -> None:
            context = self._current_prompt_history_context()
            self.prompt_history_store.append(*context, prompt)
            if context == self.prompt_history_context:
                draft = self.prompt_history_navigator.draft
                self.prompt_history_navigator.reset(
                    self.prompt_history_store.entries(*context),
                    draft,
                )

        def _associate_pending_prompt_with_context(
            self,
            context: tuple[str, str],
        ) -> None:
            prompt = self.pending_prompt
            if not prompt:
                return
            self.prompt_history_store.append(
                *context,
                prompt,
                deduplicate_last=True,
            )

        @on(PromptSubmitted)
        def _on_prompt(self, event: PromptSubmitted) -> None:
            prompt = self.query_one("#prompt", PromptEditor)
            value = event.value.strip()
            if not value:
                return
            if value.startswith("/"):
                self._clear_prompt_draft()
                self._handle_command(value)
            else:
                self._submit_task_prompt(value)
            prompt.focus()

        @on(TextArea.Changed)
        def _on_text_changed(self, event: TextArea.Changed) -> None:
            if event.text_area.id == "prompt":
                text = event.text_area.text
                try:
                    expected_index = self._prompt_history_programmatic_values.index(text)
                except ValueError:
                    self._prompt_history_programmatic_values.clear()
                    self.prompt_history_navigator.note_edit(text)
                    self.prompt_history_drafts[self.prompt_history_context] = text
                else:
                    del self._prompt_history_programmatic_values[: expected_index + 1]
                self._update_suggestions(event.text_area.text)
                self._sync_prompt_height()

        @on(PromptHistoryRequested)
        def _on_prompt_history_requested(self, event: PromptHistoryRequested) -> None:
            context = self._current_prompt_history_context()
            if context != self.prompt_history_context:
                self._load_prompt_history_context()
            prompt = self.query_one("#prompt", PromptEditor)
            text = self.prompt_history_navigator.navigate(
                event.direction,
                prompt.text,
            )
            self._replace_prompt_text(text)

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

        def _commit_synthesis_agents_control(self) -> bool:
            try:
                control = self.query_one("#config-synthesis-agents", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get(
                "config-synthesis-agents"
            ):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_synthesis([value_text])
            applied = (
                requested is not None
                and requested >= 0
                and self.synthesis_agents == requested
            )
            self._set_committed_input_value(
                control,
                str(self.synthesis_agents),
            )
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

        def _commit_subagents_limit_control(self) -> bool:
            try:
                control = self.query_one("#config-subagents-limit", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get(
                "config-subagents-limit"
            ):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_subagentslimit([value_text])
            applied = (
                requested is not None
                and requested > 0
                and getattr(self.args, "subagents_limit", None) == requested
            )
            display_value = str(
                getattr(
                    self.args,
                    "subagents_limit",
                    DEFAULT_SUBAGENTS_LIMIT,
                )
            )
            self._set_committed_input_value(control, display_value)
            return applied

        def _model_effort_control_has_pending_value(
            self,
            control: Select,
        ) -> bool:
            control_id = control.id
            if control_id not in self._committed_model_effort_values:
                return False
            return str(control.value or "") != self._committed_model_effort_values[
                control_id
            ]

        def _mark_model_effort_control_committed(
            self,
            control: Select,
        ) -> None:
            if control.id in self._committed_model_effort_values:
                self._committed_model_effort_values[control.id] = str(
                    control.value or ""
                )

        def _commit_model_control(self) -> bool:
            try:
                control = self.query_one("#config-model", Select)
            except Exception:
                return True
            requested = str(control.value or "").strip()
            current = str(getattr(self.args, "model", None) or "").strip()
            if requested != current:
                self._handle_model([requested or "clear"])
                current = str(getattr(self.args, "model", None) or "").strip()
            if current != requested:
                control.focus()
                return False
            self._mark_model_effort_control_committed(control)
            return True

        def _commit_effort_control(self) -> bool:
            # MODEL and EFFORT are one logical setting. Textual may deliver the
            # EFFORT event first, so commit the model currently shown beside it
            # before validating the requested effort.
            if not self._commit_model_control():
                return False
            try:
                control = self.query_one("#config-effort", Select)
            except Exception:
                return True
            requested = str(control.value or "").strip().lower()
            current = str(getattr(self.args, "effort", None) or "").strip().lower()
            if requested != current:
                self._handle_effort([requested or "auto"])
                current = (
                    str(getattr(self.args, "effort", None) or "").strip().lower()
                )
            if current != requested:
                control.focus()
                return False
            self._mark_model_effort_control_committed(control)
            return True

        def _commit_model_effort_controls(self) -> bool:
            try:
                model_control = self.query_one("#config-model", Select)
                effort_control = self.query_one("#config-effort", Select)
            except Exception:
                return True
            model = str(model_control.value or "").strip() or None
            effort = str(effort_control.value or "").strip().lower() or None

            # Do not route this snapshot through the command handlers: they
            # repaint the panel and could overwrite another unsubmitted input
            # before _commit_runner_inputs has read it.
            self.args.model = model
            self._mark_model_effort_control_committed(model_control)
            if self._effort_model_is_known():
                try:
                    self.model_registry.validate_effort(
                        self._model_for_effort(),
                        effort,
                    )
                except ValueError as exc:
                    self.status = str(exc)
                    self.run_info_rows = self._base_info_rows()
                    effort_control.focus()
                    return False

            self.args.effort = effort
            self._mark_model_effort_control_committed(effort_control)
            self.run_info_rows = self._base_info_rows()
            return True

        def _commit_runner_inputs(self) -> bool:
            # Select.Changed is queued. Commit the visible MODEL/EFFORT pair
            # before numeric handlers can trigger a repaint from stale args.
            if not self._commit_model_effort_controls():
                return False
            if not self._commit_agents_control():
                self.query_one("#config-agents", Input).focus()
                return False
            if not self._commit_synthesis_agents_control():
                self.query_one("#config-synthesis-agents", Input).focus()
                return False
            if not self._commit_max_parallel_control():
                self.query_one("#config-max-parallel", Input).focus()
                return False
            if not self._commit_subagents_limit_control():
                self.query_one("#config-subagents-limit", Input).focus()
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

        @on(Input.Submitted, "#config-synthesis-agents")
        def _on_synthesis_agents_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_synthesis_agents_control()

        @on(events.DescendantBlur, "#config-synthesis-agents")
        def _on_synthesis_agents_blurred(
            self,
            _event: events.DescendantBlur,
        ) -> None:
            if not self._updating_controls:
                self._commit_synthesis_agents_control()

        @on(Input.Submitted, "#config-max-parallel")
        def _on_max_parallel_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_max_parallel_control()

        @on(events.DescendantBlur, "#config-max-parallel")
        def _on_max_parallel_blurred(self, _event: events.DescendantBlur) -> None:
            if not self._updating_controls:
                self._commit_max_parallel_control()

        @on(Input.Submitted, "#config-subagents-limit")
        def _on_subagents_limit_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_subagents_limit_control()

        @on(events.DescendantBlur, "#config-subagents-limit")
        def _on_subagents_limit_blurred(
            self,
            _event: events.DescendantBlur,
        ) -> None:
            if not self._updating_controls:
                self._commit_subagents_limit_control()

        def _accept_select_event(self, event: Select.Changed) -> bool:
            """Ignore delayed Select messages that predate the current choice."""
            if self._updating_controls:
                return False
            control_key = event.select.id or str(id(event.select))
            previous_time = self._latest_select_event_time.get(control_key)
            if previous_time is not None and event.time < previous_time:
                return False
            self._latest_select_event_time[control_key] = event.time
            return True

        @on(Select.Changed, "#config-execution")
        def _on_execution_selected(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            serial = str(event.value) == "serial"
            current_serial = dict(self._tree_rows()).get("EXECUTION") == "serial"
            if serial != current_serial:
                self._handle_execution(serial=serial)

        @on(Select.Changed, "#config-subagents")
        def _on_subagents_toggled(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            value = bool(event.value)
            if value != bool(getattr(self.args, "subagents", False)):
                self._handle_subagents(["on" if value else "off"])

        @on(Select.Changed, "#config-recommend-by")
        def _on_recommend_by_selected(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            value = str(event.value)
            if value != str(
                getattr(self.args, "recommend_by", "reasoning_tokens")
            ):
                self._handle_recommendby([value])

        @on(Select.Changed, "#config-model")
        def _on_model_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            self._commit_model_control()

        @on(Select.Changed, "#config-effort")
        def _on_effort_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            self._commit_effort_control()

        @on(Select.Changed, "#config-sync-back")
        def _on_sync_back_toggled(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            current = not bool(getattr(self.args, "no_sync_back", False))
            value = bool(event.value)
            if value != current:
                self._handle_syncback(["on" if value else "off"])

        @on(Select.Changed, "#config-keep-workspaces")
        def _on_keep_workspaces_toggled(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            current = bool(getattr(self.args, "keep_workspaces", False))
            value = bool(event.value)
            if value != current:
                self._handle_keepworkspaces(["on" if value else "off"])

        @on(Select.Changed, "#config-resume")
        def _on_resume_selected(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
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
            if self.storage_preflight_inflight:
                self.status = "Cannot switch agents while checking storage"
                self._sync()
                return
            last_agent = max(self.agents, default=self.num_agents)
            self.selected_agent = min(last_agent, max(1, self.selected_agent + delta))
            self._sync()

        def _agent_kill_requested(self, idx: int) -> bool:
            event = self.agent_cancel_events.get(idx)
            return bool(event is not None and event.is_set())

        @on(RunnerEvent)
        def _on_runner_event(self, event: RunnerEvent) -> None:
            payload = event.payload
            kind = str(payload.get("type") or "")
            idx = int(payload.get("idx") or 0)
            role = normalize_agent_role(payload.get("role"))
            if kind == "synthesis_started":
                raw_indices = payload.get("indices")
                indices = (
                    [
                        int(value)
                        for value in raw_indices
                        if isinstance(value, int) and value > 0
                    ]
                    if isinstance(raw_indices, list)
                    else []
                )
                user_prompt = str(
                    payload.get("user_prompt")
                    or payload.get("prompt")
                    or self.pending_prompt
                )
                developer_instructions = str(
                    payload.get("developer_instructions") or ""
                )
                for synthesis_idx in indices:
                    pane = self.agents.get(synthesis_idx)
                    if pane is None:
                        self.agents[synthesis_idx] = AgentPane(
                            idx=synthesis_idx,
                            role=AGENT_ROLE_SYNTHESIS,
                            status="queued",
                            input_text=user_prompt,
                            execution_prompt=user_prompt,
                            developer_instructions=developer_instructions,
                        )
                    else:
                        pane.role = AGENT_ROLE_SYNTHESIS
                        pane.input_text = user_prompt
                        pane.execution_prompt = user_prompt
                        pane.developer_instructions = developer_instructions
                    self.agent_cancel_events.setdefault(
                        synthesis_idx,
                        threading.Event(),
                    )
                self.active_batch_indices = set(indices)
                self.status = f"Running {len(indices)} synthesis agents"
                self._mark_detail_dirty()
            pane = self.agents.get(idx)
            if (
                pane is None
                and idx > 0
                and role == AGENT_ROLE_SYNTHESIS
                and kind in {
                    "agent_status",
                    "agent_started",
                    "agent_tokens",
                    "agent_line",
                    "agent_finished",
                }
            ):
                pane = AgentPane(
                    idx=idx,
                    role=role,
                    input_text=self.pending_prompt,
                )
                self.agents[idx] = pane
                self.agent_cancel_events.setdefault(idx, threading.Event())
            if kind == "run_prepared":
                rows = payload.get("rows")
                if isinstance(rows, list):
                    run_rows = [(str(k), str(v)) for k, v in rows if isinstance(k, str)]
                    self._remember_run_paths(run_rows)
                    prepared = dict(run_rows)
                    merged_rows = [
                        (label, prepared.pop(label, value))
                        for label, value in self._base_info_rows()
                    ]
                    merged_rows.extend(prepared.items())
                    self.run_info_rows = self._visible_info_rows(merged_rows)
                    prepared_effort = dict(run_rows).get("EFFORT", "")
                    if self.pending_execution_args is not None and prepared_effort:
                        self.pending_execution_args.effort = (
                            None if prepared_effort == "default" else prepared_effort
                        )
            elif kind == "agent_status" and pane is not None:
                pane.role = role
                pane.status = (
                    "stopping"
                    if self._agent_kill_requested(idx)
                    else str(payload.get("status") or pane.status)
                )
                self._mark_detail_dirty(pane)
            elif kind == "agent_started" and pane is not None:
                pane.role = role
                pane.status = "stopping" if self._agent_kill_requested(idx) else "running"
                self._mark_detail_dirty(pane)
            elif kind == "agent_tokens" and pane is not None:
                value = payload.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
                if "reasoning_token_counts" in payload:
                    pane.reasoning_token_counts = normalize_reasoning_token_counts(
                        payload.get("reasoning_token_counts")
                    )
            elif kind == "agent_line" and pane is not None:
                if not self._agent_kill_requested(idx):
                    pane.status = "running"
                category, text = display_line_parts_from_output(str(payload.get("text") or ""))
                pane.append(text, category)
                self._mark_detail_dirty(pane)
            elif kind == "agent_finished" and pane is not None:
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                pane.role = normalize_agent_role(result.get("role", role))
                pane.result = result
                pane.status = str(result.get("status") or "finished")
                pane.diff_request += 1
                pane.show_diff = False
                pane.diff_loading = False
                pane.diff_text = ""
                pane.diff_error = ""
                value = result.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
                if "reasoning_token_counts" in result:
                    pane.reasoning_token_counts = normalize_reasoning_token_counts(
                        result.get("reasoning_token_counts")
                    )
                final_text = self._read_final_message(result) if pane.status == "success" else ""
                if final_text:
                    pane.final_text = final_text
                self._mark_detail_dirty(pane)
            elif kind == "synthesis_finished":
                self.active_batch_indices.clear()
                error = str(payload.get("error") or "")
                raw_indices = payload.get("indices")
                indices = raw_indices if isinstance(raw_indices, list) else []
                unresolved_status = ""
                if error:
                    unresolved_status = "error"
                elif payload.get("cancelled"):
                    unresolved_status = "cancelled"
                if unresolved_status:
                    for value in indices:
                        if not isinstance(value, int):
                            continue
                        unresolved = self.agents.get(value)
                        if unresolved is not None and unresolved.result is None:
                            unresolved.status = unresolved_status
                            self._mark_detail_dirty(unresolved)
                if error:
                    self.status = "Synthesis failed; using first-stage candidates"
                elif payload.get("cancelled"):
                    self.status = "Stopping synthesis agents"
                else:
                    self.status = "Synthesis complete"
            elif kind == "run_finished":
                self.running = False
                self.cancel_event = None
                self.active_batch_indices.clear()
                run_root = payload.get("run_root")
                if isinstance(run_root, str) and run_root:
                    self.pending_run_root = Path(run_root)
                    self.pending_workspaces_root = self.pending_run_root / "workspaces"
                if payload.get("cancelled"):
                    self._finish_cancelled_runner()
                else:
                    fallback = payload.get("best_agent") if isinstance(payload.get("best_agent"), int) else None
                    self._recompute_recommendation(fallback)
                    if not self._launch_next_candidate_batch():
                        self.status = self._completed_status()
                        self._notify_completion_if_background()
                        self._schedule_follow_up_after_completion()
            elif kind == "run_failed":
                self.running = False
                self.cancel_event = None
                self.active_batch_indices.clear()
                if self.pending_accept_agent is not None or (
                    self.queued_prompt and self.queued_agent is not None
                ):
                    self._finish_cancelled_runner()
                elif self.follow_up_queue and self._has_pending_run():
                    self._recompute_recommendation()
                    if self.recommended_agent is not None:
                        self._schedule_follow_up_after_completion()
                    else:
                        self.follow_up_ready = False
                        self.status = (
                            f"Run failed: {payload.get('message') or ''}; "
                            "queued follow-up is waiting for /retry or /more"
                        )
                else:
                    self.status = f"Run failed: {payload.get('message') or ''}"
                    if self._cleanup_after_pending_run():
                        self._clear_pending_run()
            elif kind == "candidate_batch_finished":
                self.running = False
                self.cancel_event = None
                self.active_batch_indices.clear()
                if payload.get("cancelled"):
                    self._finish_cancelled_runner()
                else:
                    self._recompute_recommendation()
                    if not self._launch_next_candidate_batch():
                        self.status = self._completed_status()
                        self._notify_completion_if_background()
                        self._schedule_follow_up_after_completion()
            elif kind == "candidate_batch_failed":
                self.running = False
                self.cancel_event = None
                for active_idx in self.active_batch_indices:
                    active_pane = self.agents.get(active_idx)
                    if active_pane is not None and active_pane.result is None:
                        active_pane.status = "error"
                        self._mark_detail_dirty(active_pane)
                self.active_batch_indices.clear()
                if self.exit_after_run or self.pending_accept_agent is not None or (
                    self.queued_prompt and self.queued_agent is not None
                ):
                    self._finish_cancelled_runner()
                elif not self._launch_next_candidate_batch():
                    self._recompute_recommendation()
                    self.status = f"Additional candidates failed: {payload.get('message') or ''}"
                    self._notify_completion_if_background()
                    self._schedule_follow_up_after_completion()
            if kind not in {"agent_line", "agent_tokens", "agent_status"}:
                self._sync()
            if kind in {
                "run_finished",
                "run_failed",
                "candidate_batch_finished",
                "candidate_batch_failed",
            } and self.exit_after_run and not self._has_pending_run():
                self.exit()

        @on(AgentDiffLoaded)
        def _on_agent_diff_loaded(self, event: AgentDiffLoaded) -> None:
            pane = self.agents.get(event.idx)
            if pane is None or pane.diff_request != event.request_id:
                return
            pane.diff_loading = False
            pane.diff_text = event.text
            pane.diff_error = event.error
            self.status = (
                f"Cannot load AGENT-{event.idx:03d} diff: {event.error}"
                if event.error
                else f"Showing AGENT-{event.idx:03d} diff"
            )
            self._mark_detail_dirty(pane)
            self._sync()

        @on(ResumeHistoryLoaded)
        def _on_resume_history_loaded(self, event: ResumeHistoryLoaded) -> None:
            if event.request_id != self.resume_history_request:
                return
            if event.rejected:
                self.status = event.error or f"Cannot resume session: {event.session_id}"
                self._sync()
                return

            self.resume_session_id = event.session_id
            self.args.resume_session_id = event.session_id
            self._load_prompt_history_context()
            self._reset_conversation_detail()
            self._refresh_effort_for_context()
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
            self._persist_workspace_settings()
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
            if self.storage_preflight_inflight:
                self.status = "Cannot clear while checking storage"
                self._sync()
                return
            if self.running:
                self.status = "Cannot clear while a run is active"
                self._sync()
                return
            if self._has_pending_run():
                if self.recommended_agent is None:
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
                pane.rejected = False
                pane.reasoning_tokens = None
                pane.reasoning_token_counts.clear()
                pane.input_text = ""
                pane.final_text = ""
                pane.result = None
                pane.clear_detail()
            self.recommended_agent = None
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
                    if self.focused.id == "config-model":
                        text = "default"
                    elif self.focused.id == "config-effort":
                        text = self.model_registry.effort_display(
                            self._model_for_effort(),
                            None,
                        )
                    else:
                        text = "NO"
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
                self._clear_prompt_draft()
            else:
                self._request_exit()

        def _request_exit(self) -> None:
            self._persist_workspace_settings()
            if self.storage_preflight_inflight:
                self.storage_preflight_request += 1
                self.storage_preflight_inflight = False
            self.follow_up_queue.clear()
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self.follow_up_source_finalized = False
            self._follow_up_countdown_second = None
            if self.running:
                self.queued_prompt = ""
                self.queued_agent = None
                self.queued_prompt_records_history = False
                self.pending_accept_agent = None
                self._discard_queued_candidate_batches()
                self.exit_after_run = True
                if self.cancel_event is not None:
                    self.cancel_event.set()
                self.status = "Stopping and cleaning up"
                self._sync()
                return
            if not self._finalize_displayed_pending(require_resume=False):
                self._sync()
                return
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
            if self.storage_preflight_inflight:
                self.status = "Storage check in progress; use /exit to cancel"
                self._sync()
                return
            if name == "/clear":
                self.action_clear_view()
                return
            if name == "/accept":
                self._handle_accept(args)
                return
            if name == "/reject":
                self._handle_reject(args)
                return
            if name == "/retry":
                self._handle_retry(args)
                return
            if name == "/more":
                self._handle_more(args)
                return
            if name in {"/synthesis", "/synthesisagents", "/synthesis-agents"}:
                self._handle_synthesis(args)
                return
            if name == "/diff":
                self._handle_diff(args)
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
            if name in {"/subagents", "/sub-agents"}:
                self._handle_subagents(args)
                return
            if name in {
                "/subagentslimit",
                "/subagents-limit",
                "/sub-agents-limit",
            }:
                self._handle_subagentslimit(args)
                return
            if name == "/serial":
                self._handle_execution(serial=True)
                return
            if name == "/parallel":
                self._handle_execution(serial=False)
                return
            if name in {"/recommendby", "/recommend-by"}:
                self._handle_recommendby(args)
                return
            if name == "/model":
                self._handle_model(args)
                return
            if name == "/effort":
                self._handle_effort(args)
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

        def _handle_accept(self, args: list[str]) -> None:
            if args:
                self.status = "Usage: /accept"
                self._sync()
                return
            if (
                self.follow_up_queue
                and self.follow_up_source_finalized
                and not self.running
            ):
                self._dispatch_follow_up()
                return
            pane = self.agents.get(self.selected_agent)
            result = pane.result if pane is not None else None
            if not self._has_pending_run() or not isinstance(result, dict):
                self.status = "No finished agent is available to accept"
                self._sync()
                return
            if result.get("status") != "success":
                self.status = f"AGENT-{self.selected_agent:03d} is not successful"
                self._sync()
                return
            if self._pending_sync_disabled():
                self.status = "Cannot accept while sync back is disabled"
                self._sync()
                return
            if self.running:
                self.pending_accept_agent = self.selected_agent
                self.queued_prompt = ""
                self.queued_agent = None
                self.queued_prompt_records_history = False
                self._discard_queued_candidate_batches()
                if self.cancel_event is not None:
                    self.cancel_event.set()
                self.status = f"Stopping remaining agents; accepting AGENT-{self.selected_agent:03d}"
                self._sync()
                return
            accepted_idx = self.selected_agent
            if self._finalize_agent(accepted_idx, archive_detail=True):
                if self.follow_up_queue:
                    self.follow_up_source_finalized = True
                    self._dispatch_follow_up()
                else:
                    self.status = f"Accepted AGENT-{accepted_idx:03d}"
            self._sync()

        def _handle_reject(self, args: list[str]) -> None:
            if args:
                self.status = "Usage: /reject"
                self._sync()
                return
            pane = self.agents.get(self.selected_agent)
            if pane is None:
                self.status = f"Unknown agent: {self.selected_agent}"
                self._sync()
                return
            if pane.rejected:
                self.status = f"AGENT-{pane.idx:03d} is already excluded from recommendations"
                self._sync()
                return
            pane.rejected = True
            if not self.running or self.recommended_agent is not None:
                self._recompute_recommendation()
            self.status = f"AGENT-{pane.idx:03d} excluded from recommendations"
            self._mark_detail_dirty(pane)
            self._sync()

        def _handle_retry(self, args: list[str]) -> None:
            if len(args) > 1:
                self.status = "Usage: /retry [agent]"
                self._sync()
                return
            idx = self.selected_agent
            if args:
                match = re.fullmatch(r"(?:agent[-_])?(\d+)", args[0].strip(), re.IGNORECASE)
                if match is None:
                    self.status = "Usage: /retry [agent]"
                    self._sync()
                    return
                idx = int(match.group(1))
            pane = self.agents.get(idx)
            result = pane.result if pane is not None else None
            if pane is None:
                self.status = f"Unknown agent: {idx}"
                self._sync()
                return
            if self._agent_has_pending_batch(idx):
                self.status = f"AGENT-{idx:03d} is already running or queued"
                self._sync()
                return
            result_status = result.get("status") if isinstance(result, dict) else pane.status
            if result_status not in {"failed", "error", "killed"}:
                self.status = f"AGENT-{idx:03d} is not failed or killed"
                self._sync()
                return
            if not self._can_extend_current_run():
                self.status = "No current question is available to retry"
                self._sync()
                return
            if not self.running and not self.candidate_batches:
                self.completion_notification_sent = False
            self.candidate_batches.append(
                CandidateBatch(
                    [idx],
                    {idx},
                    role=pane.role,
                    prompt=pane.execution_prompt or self.pending_prompt,
                    developer_instructions=pane.developer_instructions,
                )
            )
            pane.status = "retry queued"
            self._mark_detail_dirty(pane)
            if self.running:
                self.status = f"Retry queued for AGENT-{idx:03d}"
                self._sync()
            else:
                self._launch_next_candidate_batch()

        def _handle_more(self, args: list[str]) -> None:
            if len(args) != 1:
                self.status = "Usage: /more <positive integer>"
                self._sync()
                return
            try:
                count = int(args[0])
            except ValueError:
                count = 0
            if count <= 0:
                self.status = "Usage: /more <positive integer>"
                self._sync()
                return
            if not self._can_extend_current_run():
                self.status = "No current question is available for more candidates"
                self._sync()
                return

            reserved_indices = set(self.agents) | set(self.agent_cancel_events)
            start = max(reserved_indices, default=0) + 1
            indices = list(range(start, start + count))
            for idx in indices:
                self.agents[idx] = AgentPane(
                    idx=idx,
                    status="queued",
                    input_text=self.pending_prompt,
                    execution_prompt=self.pending_prompt,
                )
                self.agent_cancel_events[idx] = threading.Event()
            if not self.running and not self.candidate_batches:
                self.completion_notification_sent = False
            self.candidate_batches.append(CandidateBatch(indices))
            self._mark_detail_dirty()
            if self.running:
                self.status = f"Queued {count} additional candidates"
                self._sync()
            else:
                self._launch_next_candidate_batch()

        def _handle_diff(self, args: list[str]) -> None:
            if args and args != ["refresh"]:
                self.status = "Usage: /diff [refresh]"
                self._sync()
                return
            pane = self.agents.get(self.selected_agent)
            if pane is None:
                self.status = f"Unknown agent: {self.selected_agent}"
                self._sync()
                return
            if pane.show_diff and not args:
                pane.diff_request += 1
                pane.show_diff = False
                pane.diff_loading = False
                self.status = f"Showing AGENT-{pane.idx:03d} conversation"
                self._mark_detail_dirty(pane)
                self._sync()
                return
            result = pane.result if isinstance(pane.result, dict) else {}
            workspace_dir = result.get("workspace_dir")
            if not isinstance(workspace_dir, str) or not workspace_dir:
                self.status = f"AGENT-{pane.idx:03d} diff is available after it finishes"
                self._sync()
                return

            pane.diff_request += 1
            request_id = pane.diff_request
            pane.show_diff = True
            pane.diff_loading = True
            pane.diff_text = ""
            pane.diff_error = ""
            baseline = self._pending_workspace_path()
            candidate = Path(workspace_dir)
            self.status = f"Loading AGENT-{pane.idx:03d} diff"
            self._mark_detail_dirty(pane)
            self._sync()

            def target() -> None:
                text = ""
                error = ""
                try:
                    text = build_workspace_diff_text(baseline, candidate)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                try:
                    self.call_from_thread(
                        self.post_message,
                        AgentDiffLoaded(pane.idx, request_id, text, error),
                    )
                except RuntimeError:
                    pass

            threading.Thread(target=target, name=f"pcr-diff-{pane.idx:03d}", daemon=True).start()

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
            if self.storage_preflight_inflight:
                self.status = f"Cannot change {label} while checking storage"
                self._sync()
                return False
            if self.running:
                self.status = f"Cannot change {label} while running"
                self._sync()
                return False
            return True

        def _prepare_context_change(self, label: str) -> bool:
            if self.storage_preflight_inflight:
                self.status = f"Cannot change {label} while checking storage"
                self._sync()
                return False
            if self.running:
                self.status = f"Cannot change {label} while running"
                self._sync()
                return False
            if self._finalize_displayed_pending(require_resume=False, archive_detail=True):
                return True
            self._sync()
            return False

        def _finalize_displayed_pending(
            self,
            require_resume: bool = True,
            archive_detail: bool = False,
        ) -> bool:
            if not self._has_pending_run():
                return True
            if self._pending_sync_disabled():
                return self._discard_pending_run()
            if not any(
                isinstance(pane.result, dict) and pane.result.get("status") == "success"
                for pane in self.agents.values()
            ):
                return self._discard_pending_run()
            return self._finalize_agent(
                self.selected_agent,
                require_resume=require_resume,
                archive_detail=archive_detail,
            )

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
            preserve_completed_run = self._has_pending_run()
            self.num_agents = value
            self.args.num_agents = value
            if not preserve_completed_run:
                self.selected_agent = min(self.selected_agent, value)
                self.agents = {idx: AgentPane(idx) for idx in range(1, value + 1)}
                self.recommended_agent = None
                self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"Next run will use {value} agents")

        def _handle_synthesis(self, args: list[str]) -> None:
            if not args:
                self._show_setting(
                    f"synthesis={self.synthesis_agents}"
                )
                return
            if len(args) != 1:
                self.status = "Usage: /synthesis <non-negative integer|off>"
                self._sync()
                return
            value_text = args[0].strip().lower()
            if value_text in {"off", "none", "disable", "disabled"}:
                value = 0
            else:
                try:
                    value = int(value_text)
                except ValueError:
                    value = -1
            if value < 0:
                self.status = "Usage: /synthesis <non-negative integer|off>"
                self._sync()
                return
            if not self._prepare_config_change("synthesis agent count"):
                return
            self.synthesis_agents = value
            self.args.synthesis_agents = value
            self.run_info_rows = self._base_info_rows()
            if value:
                self._show_setting(
                    f"Next run will use {value} synthesis agents"
                )
            else:
                self._show_setting("Synthesis is disabled for the next run")

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

        def _handle_subagents(self, args: list[str]) -> None:
            current = bool(getattr(self.args, "subagents", False))
            if not args:
                self._show_setting(
                    "subagents="
                    f"{'on' if current else 'off'} "
                    f"(limit={getattr(self.args, 'subagents_limit', DEFAULT_SUBAGENTS_LIMIT)})"
                )
                return
            value = self._parse_bool_arg(args, "/subagents")
            if value is None:
                return
            if not self._prepare_config_change("nested agents"):
                return
            self.args.subagents = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(
                "subagents="
                f"{'on' if value else 'off'} "
                f"(limit={getattr(self.args, 'subagents_limit', DEFAULT_SUBAGENTS_LIMIT)})"
            )

        def _handle_subagentslimit(self, args: list[str]) -> None:
            if not args:
                self._show_setting(
                    "subagentslimit="
                    f"{getattr(self.args, 'subagents_limit', DEFAULT_SUBAGENTS_LIMIT)}"
                )
                return
            if len(args) != 1:
                self.status = "Usage: /subagentslimit <positive integer>"
                self._sync()
                return
            try:
                value = int(args[0])
            except ValueError:
                value = 0
            if value <= 0:
                self.status = "subagentslimit must be > 0"
                self._sync()
                return
            if not self._prepare_config_change("nested agent limit"):
                return
            self.args.subagents_limit = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"subagentslimit={value}")

        def _handle_execution(self, serial: bool) -> None:
            if not self._prepare_config_change("execution mode"):
                return
            self.args.serial = serial
            if not serial and getattr(self.args, "max_parallel", None) == 1:
                self.args.max_parallel = None
            self.run_info_rows = self._base_info_rows()
            self._show_setting("execution=serial" if serial else "execution=parallel")

        def _handle_recommendby(self, args: list[str]) -> None:
            if not args:
                self._show_setting(
                    f"recommendby={getattr(self.args, 'recommend_by', 'reasoning_tokens')}"
                )
                return
            try:
                value = normalize_recommend_by(args[0])
            except argparse.ArgumentTypeError as exc:
                self.status = str(exc)
                self._sync()
                return
            if not self._prepare_config_change("selection strategy"):
                return
            self.args.recommend_by = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(f"recommendby={value}")

        def _resume_model_for_effort(self) -> str | None:
            if self.resume_session_id:
                for session in self.resume_entries:
                    if str(getattr(session, "session_id", "")) == self.resume_session_id:
                        resumed_model = str(getattr(session, "model", "") or "").strip()
                        if resumed_model:
                            return resumed_model
            return None

        def _model_for_effort(self) -> str | None:
            configured = str(getattr(self.args, "model", None) or "").strip()
            return configured or self._resume_model_for_effort()

        def _effort_model_is_known(self) -> bool:
            return bool(
                getattr(self.args, "model", None)
                or not self.resume_session_id
                or self._resume_model_for_effort()
            )

        def _effort_options_for_context(self) -> list[tuple[str, str]]:
            model = (
                self._model_for_effort()
                if self._effort_model_is_known()
                else UNKNOWN_RESUME_MODEL
            )
            return self.model_registry.effort_options(
                model,
                getattr(self.args, "effort", None),
            )

        def _coerce_effort_for_model(self) -> bool:
            if not self._effort_model_is_known():
                return False
            current = getattr(self.args, "effort", None)
            if self.model_registry.effort_is_supported(
                self._model_for_effort(),
                current,
            ):
                return False
            self.args.effort = None
            return True

        def _refresh_effort_for_context(self) -> bool:
            return self._coerce_effort_for_model()

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
            effort_reset = self._refresh_effort_for_context()
            self.run_info_rows = self._base_info_rows()
            setting = f"model={value or 'default'}"
            if effort_reset:
                setting += (
                    f"; effort={self.model_registry.effort_display(self._model_for_effort(), None)}"
                )
            self._show_setting(setting)

        def _handle_effort(self, args: list[str]) -> None:
            if not args:
                self._show_setting(
                    f"effort={self.model_registry.effort_display(self._model_for_effort(), getattr(self.args, 'effort', None))}"
                )
                return
            if len(args) != 1:
                self.status = "Usage: /effort <auto|level>"
                self._sync()
                return
            if not self._prepare_config_change("effort"):
                return
            requested = args[0].strip().lower()
            value = None if requested in {"auto", "clear", "default", "none"} else requested
            if self._effort_model_is_known():
                try:
                    self.model_registry.validate_effort(
                        self._model_for_effort(), value
                    )
                except ValueError as exc:
                    self.status = str(exc)
                    self._sync()
                    return
            self.args.effort = value
            self.run_info_rows = self._base_info_rows()
            self._show_setting(
                f"effort={self.model_registry.effort_display(self._model_for_effort(), value)}"
            )

        def _handle_workspace(self, args: list[str]) -> None:
            if not args:
                self._show_setting(f"workspace={absolute_path_for_display(self.workspace)}")
                return
            workspace = Path(args[0]).expanduser().resolve()
            if not workspace.exists() or not workspace.is_dir():
                self.status = f"Workspace not found: {workspace}"
                self._sync()
                return
            if not self._prepare_context_change("workspace"):
                return
            self._persist_workspace_settings()
            self.workspace = workspace
            self.args.workspace = str(workspace)
            self.resume_session_id = ""
            self.args.resume_session_id = None
            self._workspace_config_explicit = frozenset()
            self._apply_saved_workspace_settings()
            self.num_agents = self.args.num_agents
            self.synthesis_agents = max(
                0,
                int(getattr(self.args, "synthesis_agents", 0) or 0),
            )
            self.args.synthesis_agents = self.synthesis_agents
            self.resume_session_id = (self.args.resume_session_id or "").strip()
            self.model_choices = self.model_registry.model_options(
                getattr(self.args, "model", None)
            )
            self.effort_choices = self._effort_options_for_context()
            self._load_prompt_history_context()
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
            if self._has_pending_run() and not self.running:
                self.pending_no_sync_back = not value
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
            if self._has_pending_run() and not self.running:
                self.pending_keep_workspaces = value
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
            self._submit_task_prompt(prompt)

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
            self.recommended_agent = None
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
                if not self._finalize_displayed_pending(
                    require_resume=False,
                    archive_detail=True,
                ):
                    self._sync()
                    return
                self.resume_session_id = ""
                self.args.resume_session_id = None
                self._load_prompt_history_context()
                self.resume_history_request += 1
                self.pending_resume_selector = None
                self._reset_conversation_detail()
                self._refresh_effort_for_context()
                self.run_info_rows = self._base_info_rows()
                self._refresh_resume_control()
                self.status = "Resume cleared"
                self._persist_workspace_settings()
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
                    if not self._prepare_context_change("resume session"):
                        return
                    self._select_resume_session(selector)
                    return
            if chosen is None:
                self.status = "No resumable session found"
                self._sync()
                return
            if not self._prepare_context_change("resume session"):
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

        def _pending_recommend_by(self) -> str:
            if self.pending_execution_args is not None:
                return str(
                    getattr(
                        self.pending_execution_args,
                        "recommend_by",
                        "reasoning_tokens",
                    )
                )
            return str(
                getattr(self.args, "recommend_by", "reasoning_tokens")
            )

        def _recompute_recommendation(self, fallback: int | None = None) -> None:
            candidates: list[AgentResult] = []
            saw_success = False
            for pane in self.agents.values():
                result = pane.result
                if not isinstance(result, dict) or result.get("status") != "success":
                    continue
                saw_success = True
                if pane.rejected:
                    continue
                try:
                    candidates.append(AgentResult(**result))
                except (TypeError, ValueError):
                    continue
            recommendation = select_best_result(
                candidates,
                self._pending_recommend_by(),
                warn_missing_tokens=False,
            )
            if recommendation is not None:
                self.recommended_agent = recommendation.idx
            elif saw_success:
                self.recommended_agent = None
            elif fallback is not None:
                pane = self.agents.get(fallback)
                self.recommended_agent = (
                    None if pane is not None and pane.rejected else fallback
                )
            else:
                self.recommended_agent = None

        def _completed_status(self) -> str:
            if self.recommended_agent is not None:
                return f"Done: agent_{self.recommended_agent:03d}"
            if any(
                isinstance(pane.result, dict) and pane.result.get("status") == "success"
                for pane in self.agents.values()
            ):
                return "Done: all successful agents are rejected"
            return "No successful agent"

        def _notify_completion_if_background(self) -> None:
            if self.completion_notification_sent:
                return
            self.completion_notification_sent = True
            if self.app_in_foreground:
                return
            workspace = self._pending_workspace_path()
            workspace_name = workspace.name or str(workspace)
            with contextlib.suppress(Exception):
                self.notify(
                    workspace_name,
                    title="parallel-codex-runner",
                    timeout=5,
                    markup=False,
                )
            with contextlib.suppress(Exception):
                self.bell()

        def _can_extend_current_run(self) -> bool:
            return bool(
                self.pending_prompt
                and self.pending_execution_args is not None
                and (self.running or self._has_pending_run())
            )

        def _agent_has_pending_batch(self, idx: int) -> bool:
            return idx in self.active_batch_indices or any(
                idx in batch.indices for batch in self.candidate_batches
            )

        def _discard_queued_candidate_batches(self) -> None:
            for batch in self.candidate_batches:
                for idx in batch.indices:
                    if idx in batch.retry_indices:
                        pane = self.agents.get(idx)
                        if pane is not None and isinstance(pane.result, dict):
                            pane.status = str(pane.result.get("status") or "finished")
                            self._mark_detail_dirty(pane)
                    else:
                        self.agents.pop(idx, None)
                        self.agent_cancel_events.pop(idx, None)
            self.candidate_batches.clear()
            if self.selected_agent not in self.agents and self.agents:
                self.selected_agent = min(
                    self.agents,
                    key=lambda idx: (abs(idx - self.selected_agent), idx),
                )

        def _prepare_retry_pane(self, pane: AgentPane) -> None:
            pane.attempt_history.extend(self._current_attempt_blocks(pane))
            pane.attempt_history.append(("↻", "Retry", "yellow"))
            pane.status = "queued"
            pane.rejected = False
            pane.reasoning_tokens = None
            pane.reasoning_token_counts.clear()
            pane.input_text = self.pending_prompt
            pane.final_text = ""
            pane.result = None
            pane.detail_events.clear()
            pane.lines.clear()
            pane.thought_lines.clear()
            pane.output_lines.clear()
            pane.show_diff = False
            pane.diff_loading = False
            pane.diff_text = ""
            pane.diff_error = ""
            pane.diff_request += 1
            self._mark_detail_dirty(pane)

        def _launch_next_candidate_batch(self) -> bool:
            if self.running or not self.candidate_batches:
                return False
            if (
                self.pending_execution_args is None
                or self.pending_run_root is None
                or not self.pending_prompt
            ):
                self.status = "Cannot start additional candidates: run metadata is unavailable"
                self._discard_queued_candidate_batches()
                self._sync()
                return False

            batch = self.candidate_batches.pop(0)
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
            batch.role = normalize_agent_role(batch.role)
            execution_prompt = batch.prompt or self.pending_prompt
            for idx in batch.indices:
                pane = self.agents[idx]
                pane.role = batch.role
                pane.execution_prompt = execution_prompt
                pane.developer_instructions = batch.developer_instructions
                if idx in batch.retry_indices:
                    self._prepare_retry_pane(pane)
                else:
                    pane.status = "queued"
                    pane.result = None
                    self._mark_detail_dirty(pane)
                self.agent_cancel_events[idx] = threading.Event()

            run_args = argparse.Namespace(**vars(self.pending_execution_args))
            run_args.num_agents = len(batch.indices)
            if run_args.max_parallel is not None:
                run_args.max_parallel = min(run_args.max_parallel, len(batch.indices))
            cancel_event = threading.Event()
            run_args.cancel_event = cancel_event
            run_args.agent_cancel_events = {
                idx: self.agent_cancel_events[idx] for idx in batch.indices
            }
            self.cancel_event = cancel_event
            self.active_batch_indices = set(batch.indices)
            self.running = True
            self.started_at = time.monotonic()
            self.status = (
                f"Retrying AGENT-{batch.indices[0]:03d}"
                if batch.retry_indices and len(batch.indices) == 1
                else f"Running {len(batch.indices)} additional candidates"
            )
            self._sync()

            def target() -> None:
                previous_disable = logging.root.manager.disable
                try:
                    logging.disable(logging.CRITICAL)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        run_additional_agents(
                            args=run_args,
                            prompt=execution_prompt,
                            agent_indices=batch.indices,
                            run_root=self.pending_run_root,
                            workspace=self._pending_workspace_path(),
                            resume_session_id=getattr(
                                run_args,
                                "resume_session_id",
                                None,
                            ),
                            retry_indices=batch.retry_indices,
                            progress_callback=self._post_progress,
                            cancel_event=cancel_event,
                            agent_cancel_events=run_args.agent_cancel_events,
                            agent_role=batch.role,
                            developer_instructions=(
                                batch.developer_instructions or None
                            ),
                        )
                    self._post_progress(
                        {
                            "type": "candidate_batch_finished",
                            "cancelled": cancel_event.is_set(),
                        }
                    )
                except BaseException as exc:  # noqa: BLE001
                    self._post_progress(
                        {"type": "candidate_batch_failed", "message": str(exc)}
                    )
                finally:
                    logging.disable(previous_disable)

            self.runner_thread = threading.Thread(
                target=target,
                name="pcr-tui-candidates",
                daemon=False,
            )
            self.runner_thread.start()
            return True

        def _finish_cancelled_runner(self) -> None:
            if self.pending_accept_agent is not None:
                idx = self.pending_accept_agent
                self.pending_accept_agent = None
                if self._finalize_agent(idx, archive_detail=True):
                    if self.follow_up_queue and not self.exit_after_run:
                        self.follow_up_source_finalized = True
                        self._dispatch_follow_up()
                    else:
                        self.status = f"Accepted AGENT-{idx:03d}"
                return
            if self.queued_prompt and self.queued_agent is not None:
                self._continue_queued_prompt()
                return
            self.status = "Cancelled"
            if self._cleanup_after_pending_run():
                self._clear_pending_run()

        def _start_run(self, prompt: str, record_history: bool = False) -> bool:
            if self.running:
                return self._handle_prompt_while_running(
                    prompt,
                    record_history=record_history,
                )
            if not self._commit_runner_inputs():
                self._sync()
                return False
            if self._effort_model_is_known():
                try:
                    effective_effort = self.model_registry.resolve_effort(
                        self._model_for_effort(),
                        getattr(self.args, "effort", None),
                    )
                except ValueError as exc:
                    self.status = str(exc)
                    self._sync()
                    return False
            else:
                effective_effort = getattr(self.args, "effort", None)
            if self._has_pending_run() and (
                self._pending_sync_disabled()
                or (
                    self.recommended_agent is None
                    and not any(
                        isinstance(pane.result, dict)
                        and pane.result.get("status") == "success"
                        for pane in self.agents.values()
                    )
                )
            ):
                if not self._discard_pending_run():
                    self._sync()
                    return False
            if self._has_pending_run() and not self._finalize_agent(
                self.selected_agent,
                archive_detail=True,
            ):
                self._sync()
                return False
            self._archive_command_history()
            self.running = True
            self.exit_after_run = False
            self.pending_accept_agent = None
            self.candidate_batches.clear()
            self.active_batch_indices.clear()
            self.cancel_event = threading.Event()
            self.recommended_agent = None
            self.started_at = time.monotonic()
            self.completion_notification_sent = False
            self.pending_workspace = self.workspace
            self.pending_no_sync_back = bool(getattr(self.args, "no_sync_back", False))
            self.pending_keep_workspaces = bool(getattr(self.args, "keep_workspaces", False))
            self.pending_prompt = prompt
            self.pending_prompt_records_history = record_history
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.agent_cancel_events = {
                idx: threading.Event()
                for idx in range(
                    1,
                    self.num_agents + self.synthesis_agents + 1,
                )
            }
            for pane in self.agents.values():
                pane.input_text = prompt
                pane.execution_prompt = prompt
            self.selected_agent = min(self.selected_agent, self.num_agents)
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = "Preparing agents"
            self._sync()

            run_args = argparse.Namespace(**vars(self.args))
            run_args.prompt = prompt
            run_args.prompt_file = None
            run_args.num_agents = self.num_agents
            run_args.synthesis_agents = self.synthesis_agents
            run_args.max_parallel = getattr(self.args, "max_parallel", None)
            run_args.resume = False
            run_args.resume_session_id = self.resume_session_id or None
            run_args.effort = effective_effort
            # The TUI owns selection, sync-back, and cleanup after run_once returns.
            run_args.no_sync_back = True
            run_args.keep_workspaces = True
            run_args.cancel_event = self.cancel_event
            run_args.agent_cancel_events = self.agent_cancel_events
            self.pending_execution_args = argparse.Namespace(**vars(run_args))
            # Keep the configured limit, not the first batch's effective limit.
            # This preserves max-parallel=auto and larger explicit limits for /more.
            self.pending_execution_args.max_parallel = getattr(self.args, "max_parallel", None)

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
            if record_history:
                self._record_started_prompt(prompt)
            return True

        def _successful_agent_result(self, idx: int) -> dict[str, Any] | None:
            pane = self.agents.get(idx)
            result = pane.result if pane is not None else None
            if isinstance(result, dict) and result.get("status") == "success":
                return result
            return None

        def _enqueue_follow_up(
            self,
            prompt: str,
            record_history: bool = False,
        ) -> bool:
            if self._pending_sync_disabled():
                self.status = "Cannot queue a follow-up while sync back is disabled"
                self._sync()
                return False
            self.follow_up_queue.append(
                QueuedFollowUp(prompt, record_history=record_history)
            )
            if self.running:
                self.follow_up_continue_at = None
                self.follow_up_ready = False
                self._follow_up_countdown_second = None
                suffix = "current agents continue"
            else:
                suffix = "the first queued follow-up keeps its current schedule"
            self.status = f"Queued follow-up #{len(self.follow_up_queue)}; {suffix}"
            self._sync()
            return True

        def _handle_prompt_while_running(
            self,
            prompt: str,
            record_history: bool = False,
        ) -> bool:
            if self._pending_sync_disabled():
                self.status = "Cannot continue a running agent while sync back is disabled"
                self._sync()
                return False
            if (
                self.follow_up_queue
                or self.queued_prompt
                or self.pending_accept_agent is not None
            ):
                return self._enqueue_follow_up(
                    prompt,
                    record_history=record_history,
                )
            if self._successful_agent_result(self.selected_agent) is None:
                return self._enqueue_follow_up(
                    prompt,
                    record_history=record_history,
                )
            self.queued_prompt = prompt
            self.queued_agent = self.selected_agent
            self.queued_prompt_records_history = record_history
            self.pending_accept_agent = None
            self._discard_queued_candidate_batches()
            if self.cancel_event is not None:
                self.cancel_event.set()
            self.status = f"Stopping remaining agents; continuing from AGENT-{self.selected_agent:03d}"
            self._sync()
            return True

        def _schedule_follow_up_after_completion(self) -> bool:
            if (
                not self.follow_up_queue
                or self.running
                or self.exit_after_run
                or self.storage_preflight_inflight
            ):
                return False
            idx = self.recommended_agent
            if idx is None or self._successful_agent_result(idx) is None:
                self.follow_up_continue_at = None
                self.follow_up_ready = False
                self._follow_up_countdown_second = None
                self.status = (
                    "Queued follow-up is waiting for a successful recommended Agent"
                )
                return False
            self.selected_agent = idx
            self.follow_up_continue_at = (
                time.monotonic() + FOLLOW_UP_DELAY_SECONDS
            )
            self.follow_up_ready = False
            self._follow_up_countdown_second = int(FOLLOW_UP_DELAY_SECONDS)
            self.status = (
                f"Queued follow-up starts from AGENT-{idx:03d} in "
                f"{int(FOLLOW_UP_DELAY_SECONDS)}s; ←/→ changes the Agent"
            )
            return True

        def _consume_follow_up(self, prompt: str) -> None:
            if self.follow_up_queue:
                if self.follow_up_queue[0].prompt == prompt:
                    self.follow_up_queue.pop(0)
                else:
                    for index, item in enumerate(self.follow_up_queue):
                        if item.prompt == prompt:
                            self.follow_up_queue.pop(index)
                            break
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
            if self.running or not self.follow_up_queue:
                self.follow_up_source_finalized = False
            elif self.follow_up_source_finalized:
                self.follow_up_continue_at = (
                    time.monotonic() + FOLLOW_UP_DELAY_SECONDS
                )
            self._refresh_follow_up_queue()

        def _dispatch_follow_up(self) -> bool:
            if (
                not self.follow_up_queue
                or self.running
                or self.storage_preflight_inflight
            ):
                return False
            item = self.follow_up_queue[0]
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
            if not self.follow_up_source_finalized:
                if self._successful_agent_result(self.selected_agent) is None:
                    self.follow_up_ready = True
                    self.status = (
                        f"Queued follow-up is waiting: AGENT-{self.selected_agent:03d} "
                        "is not successful"
                    )
                    self._sync()
                    return False
                if self._pending_sync_disabled():
                    self.follow_up_ready = True
                    self.status = (
                        "Queued follow-up is waiting because sync back is disabled"
                    )
                    self._sync()
                    return False
                if not self._finalize_agent(
                    self.selected_agent,
                    archive_detail=True,
                ):
                    self._sync()
                    return False
                self.follow_up_source_finalized = True

            self._archive_command_history()
            started = self._request_run_with_storage_check(
                item.prompt,
                record_history=item.record_history,
                from_follow_up_queue=True,
            )
            if not started:
                self._sync()
            return started

        def _continue_queued_prompt(self) -> None:
            prompt = self.queued_prompt
            agent_idx = self.queued_agent
            record_history = self.queued_prompt_records_history
            self.queued_prompt = ""
            self.queued_agent = None
            self.queued_prompt_records_history = False
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
            if self.follow_up_queue:
                started = self._request_run_with_storage_check(
                    prompt,
                    record_history=record_history,
                    bypass_follow_up_queue=True,
                )
            elif record_history:
                started = self._request_run_with_storage_check(
                    prompt,
                    record_history=True,
                )
            else:
                started = self._request_run_with_storage_check(prompt)
            if not started:
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
            self._discard_queued_candidate_batches()
            self.pending_run_root = None
            self.pending_workspaces_root = None
            self.pending_workspace = None
            self.pending_no_sync_back = None
            self.pending_keep_workspaces = None
            self.pending_prompt = ""
            self.pending_prompt_records_history = False
            self.pending_execution_args = None
            self.active_batch_indices.clear()
            self.pending_accept_agent = None

        def _pending_sync_disabled(self) -> bool:
            if self.pending_no_sync_back is not None:
                return self.pending_no_sync_back
            return bool(getattr(self.args, "no_sync_back", False))

        def _pending_keep_enabled(self) -> bool:
            if self.pending_keep_workspaces is not None:
                return self.pending_keep_workspaces
            return bool(getattr(self.args, "keep_workspaces", False))

        def _pending_workspace_path(self) -> Path:
            return self.pending_workspace or self.workspace

        def _discard_pending_run(self) -> bool:
            if not self._cleanup_after_pending_run():
                return False
            self._clear_pending_run()
            self.recommended_agent = None
            self.run_info_rows = self._base_info_rows()
            return True

        def _cleanup_after_pending_run(self) -> bool:
            if self._pending_keep_enabled():
                return True
            root = self.pending_workspaces_root
            if root is None:
                return True
            workspace = self._pending_workspace_path()
            try:
                if is_relative_to(root, workspace):
                    raise RuntimeError(f"refusing to delete workspace-owned path: {root}")
                cleanup_workspace_copies(workspace, root)
                if self.pending_run_root is not None:
                    remove_agent_codex_homes(self.pending_run_root / "meta")
                return True
            except Exception as exc:  # noqa: BLE001
                self.status = f"Cleanup failed: {exc}"
                return False

        def _shutdown_runner_and_cleanup(self) -> None:
            """Stop an active runner and remove copies after the UI loop exits."""
            if self._shutdown_cleanup_started:
                return
            self._shutdown_cleanup_started = True
            self.storage_preflight_request += 1
            self.storage_preflight_inflight = False

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
            workspace = self._pending_workspace_path()
            try:
                if require_resume and not result.codex_thread_id:
                    source_codex_home = Path(result.codex_home).expanduser().resolve() if result.codex_home else get_codex_home()
                    result.codex_thread_id = infer_codex_thread_id_for_result(result, source_codex_home)
                    if not result.codex_thread_id:
                        self.status = f"AGENT-{idx:03d} has no resumable session"
                        return False

                sync_best_workspace_back(Path(result.workspace_dir), workspace)

                promotion = None
                try:
                    promotion = promote_best_codex_session_to_workspace(result, workspace)
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
                    next_context = self.prompt_history_store.context_key(
                        self.workspace,
                        result.codex_thread_id,
                    )
                    if (
                        self.pending_prompt_records_history
                        and next_context != self.prompt_history_context
                    ):
                        self._associate_pending_prompt_with_context(next_context)
                    if require_resume:
                        self.resume_session_id = result.codex_thread_id
                        self._load_prompt_history_context()
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
                if self.pending_accept_agent is not None:
                    self.status = (
                        f"Stopping remaining agents; accepting "
                        f"AGENT-{self.pending_accept_agent:03d} {self._pulse()}"
                    )
                elif self.queued_prompt and self.queued_agent is not None:
                    self.status = (
                        f"Stopping remaining agents; continuing from "
                        f"AGENT-{self.queued_agent:03d} {self._pulse()}"
                    )
                else:
                    self.status = f"Working {int(time.monotonic() - self.started_at)}s {self._pulse()}"
                self._sync()
                return
            if self.follow_up_continue_at is not None:
                remaining = max(
                    0,
                    int(self.follow_up_continue_at - time.monotonic() + 0.999),
                )
                if remaining <= 0:
                    self.follow_up_continue_at = None
                    self.follow_up_ready = True
                    self._follow_up_countdown_second = None
                    self._dispatch_follow_up()
                elif remaining != self._follow_up_countdown_second:
                    self._follow_up_countdown_second = remaining
                    self._refresh_follow_up_queue()
                return
            if (
                self.follow_up_ready
                and self.follow_up_queue
                and self._successful_agent_result(self.selected_agent) is not None
                and (
                    self.follow_up_source_finalized
                    or not self._pending_sync_disabled()
                )
            ):
                self._dispatch_follow_up()

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

        def _follow_up_queue_text(self) -> str:
            if not self.follow_up_queue:
                return ""
            if self.running:
                state = "waiting for current agents"
            elif self.storage_preflight_inflight:
                state = "preparing next run"
            elif self.follow_up_continue_at is not None:
                remaining = max(
                    0,
                    int(self.follow_up_continue_at - time.monotonic() + 0.999),
                )
                state = (
                    f"next in {remaining}s from "
                    f"AGENT-{self.selected_agent:03d}"
                )
            elif self.follow_up_ready:
                state = (
                    f"ready from AGENT-{self.selected_agent:03d}; "
                    "select a successful Agent"
                )
            elif self.follow_up_source_finalized:
                state = "waiting to start from the finalized Agent"
            else:
                state = "waiting for a successful recommended Agent"

            lines: list[str] = []
            count = len(self.follow_up_queue)
            for index, item in enumerate(self.follow_up_queue, 1):
                prompt_lines = item.prompt.splitlines() or [""]
                prefix = (
                    f"↳ QUEUE {count} · {state} · {index}  "
                    if index == 1
                    else f"  {index}  "
                )
                lines.append(prefix + prompt_lines[0])
                continuation = " " * len(prefix)
                lines.extend(continuation + line for line in prompt_lines[1:])
            return "\n".join(lines)

        def _refresh_follow_up_queue(self) -> None:
            if self._selection_drag_active() or self._screen_selection_active():
                self._follow_up_queue_refresh_deferred = True
                return
            try:
                frame = self.query_one(
                    "#follow-up-queue-frame",
                    VerticalScroll,
                )
                widget = self.query_one("#follow-up-queue", Static)
            except Exception:
                return
            text = self._follow_up_queue_text()
            frame.display = bool(text)
            if not text:
                self._follow_up_queue_cache = ""
                self._follow_up_queue_items_cache = ()
                return
            content_width = max(
                20,
                int(
                    getattr(frame.content_size, "width", 0)
                    or getattr(frame.size, "width", 0)
                    or 80
                )
                - 2,
            )
            visible_lines = sum(
                max(1, (cell_len(line) + content_width - 1) // content_width)
                for line in text.splitlines()
            )
            frame.styles.height = min(6, max(1, visible_lines))
            item_key = tuple(item.prompt for item in self.follow_up_queue)
            items_changed = item_key != self._follow_up_queue_items_cache
            if text != self._follow_up_queue_cache:
                widget.update(text)
                self._follow_up_queue_cache = text
                self._follow_up_queue_items_cache = item_key
            if items_changed:
                self.call_after_refresh(frame.scroll_home, animate=False)
            self._follow_up_queue_refresh_deferred = False

        def _selected_agent_is_recommended(self) -> bool:
            pane = self.agents.get(self.selected_agent)
            return bool(
                pane is not None
                and pane.idx == self.recommended_agent
                and self._has_detail_content(pane)
            )

        def _recommend_border_colors(
            self,
            palette_frame: int | None = None,
        ) -> tuple[str, str, str, str]:
            size = len(RECOMMEND_BORDER_COLORS)
            offsets = (0, size // 4, size // 2, (size * 3) // 4)
            palette_frame = (
                self.recommend_border_frame
                if palette_frame is None
                else palette_frame
            )
            return tuple(
                RECOMMEND_BORDER_COLORS[
                    (palette_frame + offset) % size
                ]
                for offset in offsets
            )

        def _apply_recommend_border_colors(
            self,
            frame: RainbowDetailFrame,
            edge: str | None = None,
            palette_frame: int | None = None,
        ) -> None:
            top, right, bottom, left = self._recommend_border_colors(palette_frame)
            frame.set_rainbow_colors(top, right, bottom, left, edge=edge)

        def _refresh_recommend_border(
            self,
            edge: str,
            palette_frame: int,
        ) -> None:
            if self._selection_drag_active() or self._screen_selection_active():
                self._recommend_border_deferred_for_selection = True
                return
            if not self._selected_agent_is_recommended():
                self._recommend_border_deferred_for_selection = False
                return
            try:
                frame = self.query_one("#detail-frame", RainbowDetailFrame)
            except Exception:
                return
            self._recommend_border_deferred_for_selection = False
            self._apply_recommend_border_colors(
                frame,
                edge=edge,
                palette_frame=palette_frame,
            )

        def _advance_recommend_border(self) -> None:
            if not self.app_in_foreground or not self._selected_agent_is_recommended():
                return
            if self._selection_drag_active() or self._screen_selection_active():
                self._recommend_border_deferred_for_selection = True
                return
            palette_frame = (
                self.recommend_border_frame + 1
            ) % len(RECOMMEND_BORDER_COLORS)
            edge = RainbowDetailFrame.BORDER_EDGES[
                self.recommend_border_edge_index
            ]
            self.recommend_border_edge_index = (
                self.recommend_border_edge_index + 1
            ) % len(RainbowDetailFrame.BORDER_EDGES)
            self._refresh_recommend_border(edge, palette_frame)
            if self.recommend_border_edge_index == 0:
                self.recommend_border_frame = palette_frame

        def _style_detail_frame(
            self,
            frame: RainbowDetailFrame,
            pane: AgentPane | None,
            detail_visible: bool,
        ) -> None:
            recommended = bool(
                detail_visible
                and pane is not None
                and pane.idx == self.recommended_agent
            )
            if recommended:
                if not frame.rainbow_active:
                    self._apply_recommend_border_colors(frame)
                    frame.rainbow_active = True
                frame.styles.border_title_background = RECOMMEND_TITLE_BACKGROUND
                return
            frame.rainbow_active = False
            frame.styles.border = ("round", "cyan")
            frame.styles.border_title_color = "#c8edf5"
            frame.styles.border_title_background = "#101216"

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
            self._refresh_follow_up_queue()
            frame = self.query_one("#detail-frame", RainbowDetailFrame)
            pane = self.agents.get(self.selected_agent)
            detail_visible = self._has_detail_content(pane)
            frame.display = detail_visible
            self._style_detail_frame(frame, pane, detail_visible)
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
            *,
            mark_committed: bool = True,
        ) -> None:
            # Select posts Changed from its reactive watcher. Prevent it at the
            # control so delayed programmatic events cannot look like user input.
            with control.prevent(Select.Changed):
                if options is not None:
                    control.set_options(options)
                if control.value != value:
                    control.value = value
            if mark_committed:
                self._mark_model_effort_control_committed(control)

        def _sync_model_effort_control(
            self,
            control: Select,
            value: Any,
            options: list[tuple[Any, Any]] | None = None,
        ) -> None:
            if self._model_effort_control_has_pending_value(control):
                pending_value = control.value
                self._set_select_control(
                    control,
                    pending_value,
                    options,
                    mark_committed=False,
                )
                return
            self._set_select_control(control, value, options)

        def _apply_resume_choices(self, entries: list[Any]) -> None:
            self.resume_entries = entries
            self._refresh_effort_for_context()
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

            recommended_key = self.query_one("#runner-recommended-agent-key", Static)
            recommended_value = self.query_one("#runner-recommended-agent", Static)
            recommended_visible = self.recommended_agent is not None
            recommended_key.display = recommended_visible
            recommended_value.display = recommended_visible
            if recommended_visible:
                recommended_value.update(f"agent_{self.recommended_agent:03d}")

            model_select = self.query_one("#config-model", Select)
            effort_select = self.query_one("#config-effort", Select)
            current_model = str(getattr(self.args, "model", None) or "")
            display_model = (
                str(model_select.value or "")
                if self._model_effort_control_has_pending_value(model_select)
                else current_model
            )
            model_options = None
            if display_model not in {
                value for _label, value in self.model_choices
            }:
                self.model_choices = self.model_registry.model_options(display_model)
                model_options = self.model_choices
            current_effort = str(getattr(self.args, "effort", None) or "")
            display_effort = (
                str(effort_select.value or "")
                if self._model_effort_control_has_pending_value(effort_select)
                else current_effort
            )
            current_subagents = bool(getattr(self.args, "subagents", False))
            if display_model:
                effort_model = display_model
            elif self.resume_session_id:
                effort_model = (
                    self._resume_model_for_effort() or UNKNOWN_RESUME_MODEL
                )
            else:
                effort_model = None
            desired_effort_choices = self.model_registry.effort_options(
                effort_model,
                display_effort,
            )
            effort_options = None
            if desired_effort_choices != self.effort_choices:
                self.effort_choices = desired_effort_choices
                effort_options = self.effort_choices
            resume_options = None
            if self.resume_session_id not in {value for _label, value in self.resume_choices}:
                self.resume_choices = resume_select_options(
                    self.resume_entries,
                    self.resume_session_id,
                )
                resume_options = self.resume_choices

            execution_select = self.query_one("#config-execution", Select)
            subagents_select = self.query_one("#config-subagents", Select)
            recommend_by_select = self.query_one("#config-recommend-by", Select)
            sync_back_select = self.query_one("#config-sync-back", Select)
            keep_workspaces_select = self.query_one("#config-keep-workspaces", Select)
            resume_select = self.query_one("#config-resume", Select)
            controls = [
                self.query_one("#config-agents", Input),
                self.query_one("#config-synthesis-agents", Input),
                self.query_one("#config-max-parallel", Input),
                subagents_select,
                self.query_one("#config-subagents-limit", Input),
                execution_select,
                recommend_by_select,
                model_select,
                effort_select,
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
                synthesis_input = self.query_one(
                    "#config-synthesis-agents",
                    Input,
                )
                if self.focused is not synthesis_input:
                    synthesis_value = str(self.synthesis_agents)
                    synthesis_input.value = synthesis_value
                    self._committed_input_values[
                        "config-synthesis-agents"
                    ] = synthesis_value
                max_parallel_input = self.query_one("#config-max-parallel", Input)
                if self.focused is not max_parallel_input:
                    max_parallel_value = rows.get("MAX_PARALLEL", "")
                    max_parallel_input.value = max_parallel_value
                    self._committed_input_values["config-max-parallel"] = max_parallel_value
                subagents_limit_input = self.query_one(
                    "#config-subagents-limit",
                    Input,
                )
                if self.focused is not subagents_limit_input:
                    subagents_limit_value = str(
                        getattr(
                            self.args,
                            "subagents_limit",
                            DEFAULT_SUBAGENTS_LIMIT,
                        )
                    )
                    subagents_limit_input.value = subagents_limit_value
                    self._committed_input_values[
                        "config-subagents-limit"
                    ] = subagents_limit_value
                self._set_select_control(
                    execution_select,
                    rows.get("EXECUTION", "parallel"),
                )
                self._set_select_control(
                    subagents_select,
                    current_subagents,
                )
                self._set_select_control(
                    recommend_by_select,
                    str(
                        getattr(
                            self.args,
                            "recommend_by",
                            "reasoning_tokens",
                        )
                    ),
                )
                self._sync_model_effort_control(
                    model_select,
                    current_model,
                    model_options,
                )
                self._sync_model_effort_control(
                    effort_select,
                    current_effort,
                    effort_options,
                )
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
                    control.disabled = self.running or self.storage_preflight_inflight
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
            if self.recommended_agent is not None:
                rows.append(
                    ("RECOMMENDED AGENT", f"agent_{self.recommended_agent:03d}")
                )
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
                ("CODEX_BIN", str(getattr(self.args, "codex_bin", "codex"))),
                ("WORKSPACE", absolute_path_for_display(self.workspace)),
                ("RUNS_ROOT", f"pending under {absolute_path_for_display(run_base)}"),
                ("AGENTS", str(self.num_agents)),
                ("SYNTHESIS_AGENTS", str(self.synthesis_agents)),
                ("EXECUTION", execution),
                ("MAX_PARALLEL", str(max_parallel)),
                (
                    "SUBAGENTS",
                    "YES" if getattr(self.args, "subagents", False) else "NO",
                ),
                (
                    "SUBAGENTS_LIMIT",
                    str(
                        getattr(
                            self.args,
                            "subagents_limit",
                            DEFAULT_SUBAGENTS_LIMIT,
                        )
                    ),
                ),
                (
                    "RECOMMEND_BY",
                    str(
                        getattr(
                            self.args,
                            "recommend_by",
                            "reasoning_tokens",
                        )
                    ),
                ),
                (
                    "MODEL",
                    self.model_registry.model_display(
                        getattr(self.args, "model", None)
                    ),
                ),
                (
                    "EFFORT",
                    self.model_registry.effort_display(
                        self._model_for_effort(),
                        getattr(self.args, "effort", None),
                    ),
                ),
                ("SYNC_BACK", "NO" if getattr(self.args, "no_sync_back", False) else "YES"),
                ("KEEP_WORKSPACES", "YES" if getattr(self.args, "keep_workspaces", False) else "NO"),
                ("RESUME", self.resume_session_id or "NO"),
            ]

        def _detail_text(self) -> str:
            pane = self.agents.get(self.selected_agent)
            if pane is None:
                return ""
            if pane.show_diff:
                if pane.diff_loading:
                    return self._pulse()
                return pane.diff_error or pane.diff_text
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

            if pane.show_diff:
                renderable = self._diff_renderable(pane)
                self._detail_cache_key = key
                self._detail_cache_renderable = renderable
                return renderable

            blocks = self._detail_blocks(pane)
            if not blocks:
                self._detail_cache_key = key
                self._detail_cache_renderable = ""
                return ""
            parts: list[str | tuple[str, str]] = []
            for idx, (prefix, block, style) in enumerate(blocks):
                if idx:
                    parts.append("\n\n")
                formatted = self._format_prefixed_block(prefix, block)
                if style.startswith("command-"):
                    self._append_command_renderable(parts, formatted, style.removeprefix("command-"))
                else:
                    parts.append((formatted, style))
            renderable = Content.assemble(*parts)
            self._detail_cache_key = key
            self._detail_cache_renderable = renderable
            return renderable

        def _diff_renderable(self, pane: AgentPane) -> object:
            if pane.diff_loading:
                return Content.assemble((self._pulse(), "dim white"))
            text = pane.diff_error or pane.diff_text
            if not text:
                return ""
            parts: list[str | tuple[str, str]] = []
            current_style = ""
            current_lines: list[str] = []

            def flush() -> None:
                if current_lines:
                    parts.append(("".join(current_lines), current_style))
                    current_lines.clear()

            for line in text.splitlines(keepends=True):
                content = line.rstrip("\r\n")
                if content.startswith("+++") or (content.startswith("+") and not content.startswith("+++")):
                    style = "green"
                elif content.startswith("---") or (content.startswith("-") and not content.startswith("---")):
                    style = "red"
                elif content.startswith("@@"):
                    style = "cyan"
                elif re.match(r"^[AMDT]  ", content):
                    style = "yellow"
                else:
                    style = "white" if not pane.diff_error else "red"
                if current_lines and style != current_style:
                    flush()
                current_style = style
                current_lines.append(line)
            flush()
            return Content.assemble(*parts)

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
            pulse = (
                self.work_frame
                if pane.diff_loading
                or (
                    pane.status in {"running", "stopping"}
                    and (pane.has_active_command() or not pane.has_agent_text())
                )
                else None
            )
            return (
                pane.idx,
                pane.revision,
                self.detail_revision,
                self._detail_content_width(),
                pane.show_diff,
                pulse,
            )

        def _detail_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
            return [*self.detail_history, *self._pane_detail_blocks(pane), *self.command_history]

        def _has_detail_content(self, pane: AgentPane | None) -> bool:
            return pane is not None and (
                pane.show_diff or bool(self._detail_blocks(pane))
            )

        def _pane_detail_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
            return [*pane.attempt_history, *self._current_attempt_blocks(pane)]

        def _current_attempt_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
            blocks: list[tuple[str, str, str]] = []
            if pane.input_text:
                blocks.append((">", pane.input_text, "cyan"))
            event_blocks: list[tuple[str, str, str]] = []
            display_by_category = {
                "thought": ("·", "dim white"),
                "output": ("◇", "white"),
                "activity": ("•", "dim white"),
            }
            previous_category = ""
            for category, text in pane.ordered_detail_events():
                if category == "output" and pane.final_text and same_display_message(text, pane.final_text):
                    continue
                if category == "command":
                    command_active = pane.status in {"running", "stopping"}
                    text, command_state = command_detail_display(text, active=command_active)
                    prefix = self._command_marker() if command_state == "running" else "•"
                    style = f"command-{command_state}"
                else:
                    prefix, style = display_by_category.get(category, display_by_category["activity"])
                if (
                    category != "command"
                    and previous_category == category
                    and event_blocks
                    and event_blocks[-1][0] == prefix
                    and event_blocks[-1][2] == style
                ):
                    previous_prefix, previous_text, previous_style = event_blocks[-1]
                    event_blocks[-1] = (
                        previous_prefix,
                        f"{previous_text}\n{text}",
                        previous_style,
                    )
                else:
                    event_blocks.append((prefix, text, style))
                previous_category = category
            blocks.extend(event_blocks)
            if pane.status == "running" and not pane.has_agent_text() and not pane.has_active_command():
                blocks.append(("·", self._pulse(), "dim white"))
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

        def _append_command_renderable(
            self,
            parts: list[str | tuple[str, str]],
            formatted: str,
            state: str,
        ) -> None:
            marker_style = {
                "running": "bold cyan",
                "success": "bold green",
                "failed": "bold red",
                "cancelled": "bold yellow",
            }.get(state, "bold white")
            status_style = {
                "success": "green",
                "failed": "red",
                "cancelled": "yellow",
            }.get(state, "dim white")

            for index, line in enumerate(formatted.splitlines(keepends=True)):
                if index == 0:
                    marker, remainder = line[0], line[1:]
                    parts.append((marker, marker_style))
                    match = re.match(r"(\s+)(Running|Ran)(\s+)(.*)", remainder.rstrip("\n"))
                    if match is None:
                        parts.append((remainder, "white"))
                        continue
                    leading, verb, spacing, command = match.groups()
                    parts.append(leading)
                    parts.append((verb, "bold white"))
                    parts.append(spacing)
                    parts.append((command, "white"))
                    if line.endswith("\n"):
                        parts.append("\n")
                    continue

                stripped = line.lstrip()
                leading = line[: len(line) - len(stripped)]
                if stripped.startswith(("│", "└")):
                    marker, remainder = stripped[0], stripped[1:]
                    parts.append((leading + marker, "dim white"))
                    parts.append((remainder, status_style if marker == "└" else "white"))
                else:
                    parts.append((line, "white"))

        def _detail_title(self, pane: AgentPane) -> str:
            recommended = pane.idx == self.recommended_agent
            title = f"{'★ ' if recommended else ''}AGENT-{pane.idx:03d}"
            parts = []
            if pane.role == AGENT_ROLE_SYNTHESIS:
                parts.append("synthesis")
            if pane.status in {"stopping", "killed"}:
                parts.append(pane.status)
            if pane.rejected:
                parts.append("rejected")
            if pane.show_diff:
                parts.append("diff")
            if isinstance(pane.result, dict):
                seconds = format_seconds(pane.result.get("seconds"))
                if seconds:
                    parts.append(f"seconds={seconds}")
            if pane.reasoning_tokens is not None:
                parts.append(
                    format_reasoning_tokens_title(
                        pane.reasoning_tokens,
                        pane.reasoning_token_counts,
                        completed=isinstance(pane.result, dict),
                    )
                )
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

        def _command_marker(self) -> str:
            return COMMAND_SPINNER_FRAMES[self.work_frame % len(COMMAND_SPINNER_FRAMES)]


    def run_textual_tui(args: argparse.Namespace) -> int:
        app = PcrTextualApp(args)
        try:
            app.run()
        finally:
            app._persist_workspace_settings()
            app._shutdown_runner_and_cleanup()
        return 0
