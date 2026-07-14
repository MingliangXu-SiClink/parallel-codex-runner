from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


def read_effective_codex_developer_instructions(
    codex_bin: str,
    codex_home: Path,
    workspace: Path,
    timeout: float = 15.0,
) -> Optional[str]:
    """Ask Codex to resolve its effective developer instructions for a workspace."""
    codex_home = codex_home.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    try:
        process = subprocess.Popen(
            [codex_bin, "app-server", "--stdio"],
            cwd=str(workspace),
            env=env,
            text=True,
            encoding="utf-8",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "cannot start Codex app-server to preserve developer instructions: "
            f"{exc}"
        ) from exc

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    messages: queue.Queue[tuple[str, Optional[str]]] = queue.Queue()
    stderr_tail: list[str] = []

    def read_stream(name: str, stream: Any) -> None:
        try:
            for line in stream:
                messages.put((name, line))
        finally:
            messages.put((name, None))

    readers = [
        threading.Thread(
            target=read_stream,
            args=("stdout", process.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=read_stream,
            args=("stderr", process.stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + max(0.1, float(timeout))

    def failure(message: str) -> RuntimeError:
        detail = "".join(stderr_tail)[-2000:].strip()
        suffix = f": {detail}" if detail else ""
        return RuntimeError(
            f"{message}{suffix}. Upgrade Codex CLI before running synthesis."
        )

    def send(payload: Dict[str, Any]) -> None:
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise failure("Codex app-server closed during config resolution") from exc

    def response(request_id: int) -> Dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise failure("Codex app-server config resolution timed out")
            try:
                source, line = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise failure("Codex app-server config resolution timed out") from exc
            if source == "stderr":
                if line:
                    stderr_tail.append(line)
                continue
            if line is None:
                raise failure("Codex app-server exited before resolving config")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise failure("Codex app-server returned malformed JSON") from exc
            if not isinstance(payload, dict):
                raise failure("Codex app-server returned an invalid JSON message")
            if payload.get("id") != request_id:
                continue
            error = payload.get("error")
            if error is not None:
                raise failure(
                    (
                        "Codex app-server rejected config/read"
                        if request_id == 1
                        else "Codex app-server initialization failed"
                    )
                    + f" ({error})"
                )
            result = payload.get("result")
            if not isinstance(result, dict):
                raise failure("Codex app-server returned an invalid response")
            return result

    try:
        send(
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "parallel_codex_runner",
                        "title": "Parallel Codex Runner",
                        "version": "0.1.5",
                    }
                },
            }
        )
        response(0)
        send({"method": "initialized", "params": {}})
        send(
            {
                "method": "config/read",
                "id": 1,
                "params": {
                    "includeLayers": False,
                    "cwd": str(workspace),
                },
            }
        )
        result = response(1)
        config = result.get("config")
        if not isinstance(config, dict):
            raise failure("Codex app-server config/read omitted config")
        value = config.get("developer_instructions")
        if value is not None and not isinstance(value, str):
            raise failure(
                "Codex app-server returned non-string developer_instructions"
            )
        return value
    finally:
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            process.wait()
        for reader in readers:
            reader.join(timeout=0.2)


def merge_codex_developer_instructions(
    existing_instructions: Optional[str],
    additional_instructions: Optional[str],
) -> Optional[str]:
    """Append PCR instructions without replacing Codex's effective guidance."""
    existing = existing_instructions
    additional = str(additional_instructions or "")
    if existing and additional.strip():
        return f"{existing}\n\n{additional}"
    return existing or (additional if additional.strip() else None)


def build_codex_command(
    codex_bin: str,
    help_text: str,
    final_message_path: Path,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    resume_session_id: Optional[str] = None,
    developer_instructions: Optional[str] = None,
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

    config_flag = (
        "--config"
        if flag_supported(help_text, "--config")
        else "-c"
        if short_flag_supported(help_text, "-c")
        else None
    )
    caps["config"] = config_flag is not None
    caps["effort"] = caps["config"]
    caps["developer_instructions"] = caps["config"]
    if effort:
        if caps["effort"]:
            assert config_flag is not None
            cmd.extend(
                [config_flag, f"model_reasoning_effort={json.dumps(effort)}"]
            )
        else:
            _warn(
                "当前 Codex CLI help 中未检测到 --config；忽略 --effort {}",
                effort,
            )

    if developer_instructions and developer_instructions.strip():
        if config_flag is None:
            raise RuntimeError(
                "当前 Codex CLI 不支持 --config，无法安全注入 synthesis developer instructions。"
            )
        cmd.extend(
            [
                config_flag,
                "developer_instructions="
                + json.dumps(developer_instructions, ensure_ascii=False),
            ]
        )

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
