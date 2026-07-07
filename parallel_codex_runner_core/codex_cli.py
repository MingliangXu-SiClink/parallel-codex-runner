from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


def _warn(message: str, *args: object) -> None:
    try:
        from .app import logger
    except Exception:
        return
    logger.warning(message, *args)


def read_codex_help(codex_bin: str, args: Sequence[str], label: str) -> str:
    try:
        completed = subprocess.run(
            [codex_bin, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"找不到 codex 命令：{codex_bin!r}。请确认 Codex CLI 已安装且在 PATH 中。") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"执行 `{label}` 超时。") from exc
    help_text = completed.stdout or ""
    if completed.returncode not in (0, None):
        _warn("`{}` returned code {}", label, completed.returncode)
    return help_text


def read_codex_exec_help(codex_bin: str) -> str:
    return read_codex_help(codex_bin, ["exec", "--help"], "codex exec --help")


def read_codex_exec_resume_help(codex_bin: str) -> str:
    help_text = read_codex_help(codex_bin, ["exec", "resume", "--help"], "codex exec resume --help")
    if "Usage:" not in help_text or "resume" not in help_text.lower():
        raise SystemExit("当前 Codex CLI 不支持 `codex exec resume`；请升级 Codex CLI 后再使用 --resume。")
    return help_text


def flag_supported(help_text: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


def short_flag_supported(help_text: str, flag: str) -> bool:
    return any(token in help_text for token in (f"{flag},", f"{flag} ", f"{flag}\t"))


def build_codex_command(
    codex_bin: str,
    help_text: str,
    final_message_path: Path,
    model: Optional[str] = None,
    resume_session_id: Optional[str] = None,
) -> Tuple[List[str], Dict[str, bool]]:
    cmd: List[str] = [codex_bin, "exec"]
    if resume_session_id:
        cmd.append("resume")
    caps: Dict[str, bool] = {"resume": resume_session_id is not None}

    caps["json"] = flag_supported(help_text, "--json")
    if caps["json"]:
        cmd.append("--json")

    caps["output_last_message"] = flag_supported(help_text, "--output-last-message")
    if caps["output_last_message"]:
        cmd.extend(["--output-last-message", str(final_message_path)])

    model_flag = "--model" if flag_supported(help_text, "--model") else "-m" if short_flag_supported(help_text, "-m") else None
    caps["model"] = model_flag is not None
    if model:
        if caps["model"]:
            assert model_flag is not None
            cmd.extend([model_flag, model])
        else:
            _warn("当前 Codex CLI help 中未检测到 --model；忽略 --model {}", model)

    caps["dangerously_bypass"] = flag_supported(help_text, "--dangerously-bypass-approvals-and-sandbox")
    caps["sandbox"] = flag_supported(help_text, "--sandbox")
    caps["ask_for_approval"] = flag_supported(help_text, "--ask-for-approval")
    caps["skip_git_repo_check"] = flag_supported(help_text, "--skip-git-repo-check")

    if caps["dangerously_bypass"]:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        if caps["sandbox"]:
            cmd.extend(["--sandbox", "danger-full-access"])
        if caps["ask_for_approval"]:
            cmd.extend(["--ask-for-approval", "never"])

    if caps["skip_git_repo_check"]:
        cmd.append("--skip-git-repo-check")

    if resume_session_id:
        cmd.extend([resume_session_id, "-"])
    else:
        cmd.append("-")
    return cmd, caps
