import json
import os
from pathlib import Path

import pytest

from parallel_codex_runner_core import app
from parallel_codex_runner_core.codex_app_server import parse_exec_command


@pytest.mark.parametrize("idx,role,resume,retry,mode", [
    (1, "candidate", None, False, "isolated"),
    (5, "candidate", "existing-session", False, "isolated"),
    (6, "synthesis", "existing-session", False, "isolated"),
    (3, "candidate", "existing-session", True, "isolated"),
    (1, "candidate", None, False, "source"),
    (1, "candidate", "existing-session", True, "source"),
])
@pytest.mark.parametrize("transport", ["exec", "app-server"])
def test_preparation_injects_remote_access_and_identity_into_codex_transport(
    tmp_path, monkeypatch, idx, role, resume, retry, mode, transport,
):
    monkeypatch.setenv("PCR_CODEX_TRANSPORT", transport)
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "input.txt").write_text("user data\n")
    run_root = tmp_path / "pcr-unique-run"
    agent_workspace = app.agent_execution_workspace(
        workspace, run_root / "workspaces", idx, mode,
    )
    meta = run_root / "meta" / f"agent_{idx:03d}"
    if retry:
        (meta / "codex_home").mkdir(parents=True)
        if mode == "isolated":
            agent_workspace.mkdir(parents=True)
            (agent_workspace / "previous.txt").touch()

    def prepare_home(source, destination, current_workspace, session, **kwargs):
        assert current_workspace == agent_workspace
        assert session == resume
        destination.mkdir(parents=True)

    monkeypatch.setattr(app, "prepare_agent_codex_home", prepare_home)
    existing = "Project-specific remote restrictions must remain in force."
    monkeypatch.setattr(app, "read_effective_codex_developer_instructions", lambda *a: existing)
    args = app.parse_args(["remote task", "--workspace", str(workspace)])
    args.workspace_mode = mode
    help_text = (
        "Usage: codex exec --config --json --output-last-message "
        "--dangerously-bypass-approvals-and-sandbox"
    )
    command, _ = app._prepare_additional_agent(
        args=args, idx=idx, run_root=run_root, workspace=workspace,
        workspaces_root=run_root / "workspaces", meta_root=run_root / "meta",
        help_text=help_text, real_codex_home=tmp_path / "real-home",
        effective_effort=None, resume_session_id=resume, retry=retry,
        progress_callback=None, agent_role=role,
        developer_instructions="Review candidate results." if role == "synthesis" else None,
        instruction_resolver=app._AgentDeveloperInstructionResolver(), initial=True,
    )
    server_command, params, _, session = parse_exec_command(command)
    overrides = [item for item in server_command if item.startswith("developer_instructions=")]
    if transport == "exec":
        instructions = json.loads(overrides[0].split("=", 1)[1])
        assert instructions.startswith(existing)
    else:
        assert not overrides
        instructions = json.loads((meta / "developer_instructions.json").read_text())
        assert existing not in instructions
    assert "Every candidate and synthesis Agent may use SSH" in instructions
    assert f".pcr/{run_root.name}/agent_{idx:03d}" not in instructions
    assert "The user's remote project structure takes precedence" in instructions
    assert json.dumps({"run": run_root.name, "agent": idx}) in instructions
    assert "otherwise candidate Agent 1" in instructions
    assert "single-Agent continuation uses that selected Agent as its sole executor" in instructions
    assert "Other candidates and synthesis Agents must only inspect or propose" in instructions
    assert "supersedes older default remote namespace guidance" in instructions
    assert "Do not move existing execution directories" in instructions
    assert "lock serializes writes but does not authorize repeating" in instructions
    assert str(agent_workspace.resolve()) in instructions
    assert "only writable local project workspace" in instructions
    assert "loopback SSH" in instructions
    assert "Existing remote access controls and project-specific restrictions still apply." in instructions
    assert "not automatically adopted, rolled back, or removed" in instructions
    assert "only writable project workspace" not in instructions
    assert session == resume
    assert params["sandbox"] == "danger-full-access"
    assert params["approvalPolicy"] == "never"
    assert (workspace / "input.txt").read_text() == "user data\n"
    if retry and mode == "isolated":
        assert not (agent_workspace / "previous.txt").exists()


def test_execution_identity_is_stable_but_does_not_impose_remote_paths(tmp_path):
    workspace = tmp_path / "source"

    def instructions(run, idx):
        return app.workspace_isolation_developer_instructions(
            workspace, run_root=tmp_path / run, agent_idx=idx,
        )

    first = instructions("run-a", 1)
    assert first == instructions("run-a", 1)
    assert '"run": "run-a", "agent": 1' in first
    assert '"run": "run-a", "agent": 2' in instructions("run-a", 2)
    assert '"run": "run-b", "agent": 1' in instructions("run-b", 1)
    for text in [first, instructions("run-a", 2), instructions("run-b", 1)]:
        assert ".pcr/" not in text
        assert "not a remote directory naming rule" in text
        assert "existing completion evidence" in text
        assert "if ownership or prior completion is uncertain, do not mutate" in text


def test_local_paths_and_execution_identity_are_quoted(tmp_path):
    workspace = tmp_path / 'local "workspace"'
    run_root = tmp_path / 'run "quoted"'
    instructions = app.workspace_isolation_developer_instructions(
        workspace, run_root=run_root, agent_idx=1,
    )
    assert json.dumps(str(workspace.resolve()), ensure_ascii=False) in instructions
    assert json.dumps({"run": run_root.name, "agent": 1}, ensure_ascii=False) in instructions


def test_agent_environment_retains_existing_ssh_access(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "ssh-agent.sock"))
    env = app.agent_process_environment(tmp_path / "workspace", tmp_path / "private-codex-home")
    assert env["HOME"] == os.environ["HOME"]
    assert env["SSH_AUTH_SOCK"] == os.environ["SSH_AUTH_SOCK"]
    assert env["CODEX_HOME"] == str((tmp_path / "private-codex-home").resolve())
