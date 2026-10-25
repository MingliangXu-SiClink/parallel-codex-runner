from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from .codex_cli import format_fast_mode
from rich.cells import cell_len

TEXTUAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "show all TUI commands"),
    ("/status", "show current run configuration"),
    ("/config", "show current run configuration"),
    ("/accept", "finalize the currently displayed successful agent"),
    ("/reject", "exclude the currently displayed agent from recommendations"),
    ("/retry [agent]", "rerun a failed or killed agent on a fresh workspace"),
    ("/more <n>", "add more candidates for the current question"),
    ("/queue [n]", "edit, remove, or reorder queued follow-up questions"),
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
        "/followupdelay <seconds>",
        "set the review delay before a queued follow-up starts",
    ),
    (
        "/synthesis <n|off>",
        "set synthesis agents before the synthesis stage starts",
    ),
    (
        "/synthesismodel <name|default>",
        "set the stage-two model before synthesis starts",
    ),
    (
        "/synthesiseffort <auto|level>",
        "set stage-two reasoning effort before synthesis starts",
    ),
    (
        "/synthesisfast <auto|on|off>",
        "set stage-two Fast mode before synthesis starts",
    ),
    ("/subagents <on|off>", "allow or block nested Codex agents for the next run"),
    ("/subagentslimit <n>", "limit nested Codex agents within each PCR agent"),
    ("/model <name|clear>", "set or clear the Codex model for the next run"),
    ("/effort <auto|level>", "set a model-supported reasoning effort for the next run"),
    ("/fast <on|off|auto>", "select Fast, Standard, or inherited Codex speed"),
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
RUNNER_TWO_PAIR_MIN_WIDTH = 96
RUNNER_THREE_PAIR_MIN_WIDTH = 150


def _column_major_indices(item_count: int, column_count: int) -> tuple[int, ...]:
    """Return row-major placement indices for column-major reading order."""
    if item_count <= 0 or column_count <= 0:
        return ()
    row_count = (item_count + column_count - 1) // column_count
    return tuple(
        index
        for row in range(row_count)
        for column in range(column_count)
        if (index := column * row_count + row) < item_count
    )


FOLLOW_UP_CONFIG_CONTROL_ID = "config-follow-up-delay"
SYNTHESIS_CONFIG_CONTROL_IDS = frozenset(
    {
        "config-synthesis-agents",
        "config-synthesis-model",
        "config-synthesis-effort",
        "config-synthesis-fast",
    }
)
SYNTHESIS_INFO_LABELS = frozenset(
    {
        "SYNTHESIS_AGENTS",
        "SYNTHESIS_MODEL",
        "SYNTHESIS_EFFORT",
        "SYNTHESIS_FAST",
    }
)
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
    "•",
    "◦",
)
DETAIL_INPUT_MARKER = "›"
DETAIL_AGENT_MARKER = "•"
DETAIL_ACTIVITY_MARKER = "◦"
DETAIL_STYLE_PREFIX = "detail-"
DETAIL_DIVIDER_STYLE = "dim #405466"
DETAIL_HEADING_STYLE = "bold #c8edf5"
DETAIL_BULLET_STYLE = "bold #5aa9bd"
DETAIL_CODE_STYLE = "#b7c8e6"
DETAIL_CODE_FENCE_STYLE = "bold #e9c46a"
DETAIL_TEXT_STYLES: dict[str, tuple[str, str]] = {
    # Keep roles distinct without turning the detail pane into a rainbow.
    "input": ("bold #ffd27a", "#f0c674"),
    "thought": ("bold #8fb3ff", "#8fa4bd"),
    "output": ("bold #5aa9bd", "#e7ebf2"),
    "activity": ("#6faec0", "#8091a3"),
    "live": ("bold #5aa9bd", "#8091a3"),
}
TUI_TIPS: tuple[str, ...] = (
    "输入 / 可查看并补全命令。",
    "输入框为空时，←/→ 可切换 Agent；有内容时将鼠标移至 Agent 区域也可切换。",
    "Shift-Enter 可在输入框中换行。",
    "选中Agent栏目文本即可复制。",
    "使用 /resume 可载入之前的 Codex 对话。",
    "后续提问会从当前显示的已完成 Agent 继续。",
    "某个 Agent 完成后，即可从该 Agent 继续提问。",
    "AGENTS、MODEL 等运行配置只作用于下一轮，不会选择当前 Agent。",
    "PCR 会按 workspace 记住顶部配置，下次打开同一目录时自动恢复。",
    "EFFORT 会随 MODEL 更新可选等级，auto 会选择兼容的推理等级。",
    "FAST 的 AUTO 会在括号内显示继承的档位；Fast 会更快消耗 credits。",
    "带 ★ 和彩虹边框的 Agent 是当前推荐结果。",
    "退出或切换 WORKSPACE、RESUME 时，会采用当前显示的 Agent。",
    "输入 /accept 可立即采用当前 Agent。",
    "输入 /reject 可将当前 Agent 排除在推荐范围外。",
    "输入 /retry 可重跑失败或被终止的 Agent；候选阶段有空闲并发位时会立即重跑。",
    "输入 /more 3 可为当前问题追加 3 个候选 Agent。",
    "点击 QUEUE 中的问题，或输入 /queue，可编辑、取消或调整执行顺序。",
    "使用 /synthesis 3 可在候选完成后运行 3 个独立综合 Agent。",
    "候选运行期间仍可调整 SYNTHESIS_AGENTS、SYNTHESIS_MODEL、SYNTHESIS_EFFORT 和 SYNTHESIS_FAST。",
    "综合 Agent 会审核全部成功候选；你仍可采用任意成功 Agent。",
    "输入 /diff 可切换当前 Agent 的完整文件差异。",
    "TUI 不在前台时，所有 Agent 结束后会触发完成通知，失败也算结束。",
    "Ctrl-C 会依情境执行复制、清空输入或退出。",
    "Command-Z / Shift-Command-Z 可撤销或重做输入；Command-A/X/C/V 支持常用编辑操作。",
    "Option-←/→ 按词移动，Command-←/→/↑/↓ 跳到行或全文边界；配合 Shift 可选择文本。",
    "KEEP_WORKSPACES 可保留候选工作目录。",
    "SYNC_BACK 控制是否同步选中的结果至工作区。",
    "注意本项目默认以Codex Full Access 权限运行。",
    "运行中输入 /kill，可终止当前显示且正在运行的 Agent，排队 Agent 会正常加入队列。",
    "输入框中按 ↑/↓ 可浏览当前 Workspace 与 Session 的输入历史。",
    "大型工作区会在复制前估算空间，并在预计超过 5 GiB 时请求确认。",
    "SUBAGENTS 默认关闭；启用后可用 SUBAGENTS_LIMIT 控制每个 PCR Agent 的嵌套数量。",
    "当前 Agent 未完成时提交的新问题会排队；FOLLOW_UP_DELAY 控制自动续接前的选择时间。",
)


def fast_control_value(value: bool | None) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    return "auto"


def fast_value_from_control(value: Any) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    return None


def fast_setting_value(
    value: bool | None,
    configured_service_tier: str | None,
) -> str:
    if value is not None:
        return fast_control_value(value)
    return format_fast_mode(None, configured_service_tier).lower()


def command_suggestions(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text.startswith("/"):
        return []
    return [
        f"{command}  {description}"
        for command, description in TEXTUAL_COMMANDS
        if command.startswith(text)
    ][:MAX_SUGGESTIONS]


_PROMPT_CONTROL_TRANSLATION = {
    codepoint: None
    for codepoint in (
        *range(0, 9),
        11,
        12,
        *range(14, 32),
        127,
        *range(128, 160),
    )
}


def normalize_prompt_text(value: str) -> str:
    """Keep literal prompt text while removing controls unsafe for TUI rendering."""
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    return text.translate(_PROMPT_CONTROL_TRANSLATION)


def _recorded_history_context_kwargs(
    context: tuple[str, str] | None,
) -> dict[str, tuple[str, str]]:
    return {"recorded_history_context": context} if context is not None else {}


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
            "Command-Z / Shift-Command-Z undo or redo prompt edits; Command-A/X/C/V provide standard editing.",
            "Option-Left/Right moves by word; Command-Arrows move to line or document edges; add Shift to select.",
        ]
    )
    return "\n".join(lines)


HELP_TEXT = build_help_text()


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
        or "orphan custom tool call output for call id:" in normalized
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


def fold_text_by_cells(text: str, width: int) -> str:
    text = normalize_prompt_text(text)
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
