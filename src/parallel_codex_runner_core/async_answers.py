"""Deliver an explicitly committed async answer after its original native owner is idle."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .agent_questions import QuestionError, QuestionStore, async_reply
from .codex_app_server import parse_exec_command
from .plugin.lifecycle import pid_is_alive, read_json, write_json_atomic


def run_idle_answer(run, agent: int, request_id: str, previous: dict[str, Any], report, cancel, agent_cancel):
    from .workspace import source_workspace_lease

    workspace = previous.get("workspace_dir")
    if not workspace:
        raise QuestionError("The original Agent workspace is unavailable")
    with source_workspace_lease(Path(workspace)):
        return _deliver_idle_answer(run, agent, request_id, previous, report, cancel, agent_cancel)


def _deliver_idle_answer(run, agent, request_id, previous, report, cancel, agent_cancel):
    from .app import run_one_agent, copy_codex_support_entries, get_codex_home, scrub_codex_home_support_entries

    meta = Path(run.run_root) / "meta" / f"agent_{agent:03d}"
    store = QuestionStore(meta)
    record = store.get(request_id)
    prepared = read_json(meta / "command.json")
    recovery = read_json(meta / "execution_recovery.json")
    home = Path(prepared.get("codex_home") or "/nonexistent")
    if (record["kind"] != "async" or record["delivery"] != "queued"
        or recovery.get("stage") != "completed" or recovery.get("thread_id") != record["thread_id"]
        or type(recovery.get("native_pid")) is not int or pid_is_alive(recovery.get("native_pid"))
        or prepared.get("command") != previous.get("command")
        or prepared.get("cwd") != previous.get("workspace_dir")
        or prepared.get("codex_home") != previous.get("codex_home")
        or home.is_symlink() or home.resolve() != meta.resolve() / "codex_home"
        or cancel.is_set() or agent_cancel.is_set() or (meta / "execution_stop").exists()):
        raise QuestionError("The original async answer execution changed; no reply was submitted")
    server, params, output, _ = parse_exec_command(prepared["command"])
    if output.resolve() != (meta / "final_message.md").resolve():
        raise QuestionError("The original Agent output location changed")
    command = [server[0], "exec", "resume", *server[2:], "--json", "--output-last-message", str(output)]
    if params.get("model"):
        command += ["--model", params["model"]]
    if params.get("sandbox") == "danger-full-access":
        command += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        command += ["--sandbox", params["sandbox"], "--ask-for-approval", params["approvalPolicy"]]
    command += [record["thread_id"], "-"]
    archive = meta / "answers" / request_id
    archive.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_json_atomic(archive / "previous_result.json", previous)
    if not store.async_delivery(request_id, "queued", "sending"):
        raise QuestionError("This answer is already being delivered")
    # Keep the complete original receipt/result. Only this new, user-confirmed
    # answer gets a new native turn; no workspace preparation or adoption runs.
    for name in ("execution_recovery.json", "command.json", "status.json", "final_message.md"):
        path = meta / name
        if path.exists():
            path.rename(archive / name)

    def progress(event):
        if event.get("type") == "agent_line":
            try:
                native = json.loads(event.get("text", ""))
                if native.get("type") == "turn.started":
                    store.async_delivery(request_id, "sending", "sent")
            except (ValueError, TypeError):
                pass
        report(event)

    try:
        copy_codex_support_entries(get_codex_home(), home)
        return asyncio.run(run_one_agent(agent, Path(prepared["cwd"]), meta,
            home, async_reply(record, record["answers"]), command,
            progress_callback=progress, cancel_event=cancel, agent_cancel_event=agent_cancel,
            agent_role=prepared.get("role", "candidate")))
    finally:
        store.async_delivery(request_id, "sending", "unknown")
        scrub_codex_home_support_entries(home)
