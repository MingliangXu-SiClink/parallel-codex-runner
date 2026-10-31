import sys
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

from parallel_codex_runner_core import app
from parallel_codex_runner_core import workspace as workspace_core
from parallel_codex_runner_core.finalization import finalize_candidate_workspace
from parallel_codex_runner_core.models import AgentResult
from parallel_codex_runner_core.plugin.artifacts import ArtifactError, ArtifactStore
from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.plugin_runtime import PluginRunManager, PluginRunError
from parallel_codex_runner_core.workspace import (
    cleanup_workspace_copies,
    cleanup_workspace_copy,
    execution_workspace_mode,
    source_workspace_lease,
    sync_best_workspace_back,
)


@pytest.mark.parametrize("agents,synthesis,sync,keep,expected", [
    (1, 0, True, False, "source"),
    (1, 0, False, False, "isolated"),
    (1, 0, True, True, "isolated"),
    (1, 1, True, False, "isolated"),
    (2, 0, True, False, "isolated"),
])
def test_execution_mode(agents, synthesis, sync, keep, expected):
    assert execution_workspace_mode(agents, synthesis, sync, keep) == expected


@pytest.fixture
def local_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("PCR_CODEX_TRANSPORT", "exec")
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "existing.txt").write_text("user edit\n")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setattr(app, "get_codex_home", lambda: codex_home)
    monkeypatch.setattr(app, "read_codex_exec_help", lambda *_: "Usage: codex exec")
    monkeypatch.setattr(app, "read_effective_codex_developer_instructions", lambda *a, **k: None)
    monkeypatch.setattr(app.CodexModelCacheManager, "populate", lambda *_: False)
    command = [sys.executable, "-c", (
        "from pathlib import Path; import sys; "
        "Path('cwd.txt').write_text(str(Path.cwd())); "
        "Path('reply.txt').write_text(sys.stdin.read()); "
        "print('finished')"
    )]
    monkeypatch.setattr(app, "build_codex_command", lambda *a, **k: (command, {}))
    args = app.parse_args(["prompt", "-n", "1", "--synthesis-agents", "0", "--workspace", str(workspace),
                           "--runs-dir", str(tmp_path / "runs")])
    return args, workspace


def test_cli_runs_in_source_and_never_syncs_it_to_itself(local_agent, monkeypatch):
    args, workspace = local_agent
    sync = Mock(side_effect=AssertionError("must not sync source to itself"))
    monkeypatch.setattr(app, "sync_best_workspace_back", sync)
    assert app.run_once(args, "first", progress_callback=lambda _: None, print_output=False) == 0
    assert (workspace / "cwd.txt").read_text() == str(workspace.resolve())
    assert (workspace / "existing.txt").read_text() == "user edit\n"
    assert (workspace / "reply.txt").read_text() == "first"
    root = next(Path(args.runs_dir).iterdir())
    assert not (root / "workspaces").exists()
    sync.assert_not_called()


def test_source_agent_starts_when_baseline_temp_directory_disappears(local_agent, monkeypatch):
    args, workspace = local_agent
    args.workspace_mode = "source"
    args.no_sync_back = True
    args.keep_workspaces = True
    transient = workspace / "outputs" / ".work" / "processing"
    transient.mkdir(parents=True)
    (transient / "frame.png").write_bytes(b"frame")
    original = workspace_core.os.scandir
    removed = []

    def scandir(path):
        if not isinstance(path, int) and workspace_core.os.fspath(path) == str(transient):
            workspace_core.shutil.rmtree(transient)
            removed.append(True)
        return original(path)

    monkeypatch.setattr(workspace_core.os, "scandir", scandir)
    assert app.run_once(args, "continue", progress_callback=lambda _: None, print_output=False) == 0
    assert removed == [True]
    assert (workspace / "reply.txt").read_text() == "continue"
    assert (workspace / "existing.txt").read_text() == "user edit\n"
    root = next(Path(args.runs_dir).iterdir())
    assert (root / "workspaces/baseline/existing.txt").read_text() == "user edit\n"
    assert not (root / "workspaces/baseline/outputs/.work/processing").exists()


def test_deferred_review_retry_and_cleanup_keep_source_edits(local_agent):
    args, workspace = local_agent
    args.workspace_mode = "source"
    args.no_sync_back = True
    args.keep_workspaces = True
    assert app.run_once(args, "first", progress_callback=lambda _: None, print_output=False) == 0
    root = next(Path(args.runs_dir).iterdir())
    assert not (root / "workspaces" / "agent_001").exists()
    assert (root / "workspaces" / "baseline" / "existing.txt").read_text() == "user edit\n"
    (workspace / "between.txt").write_text("do not roll back")
    with pytest.raises(FileNotFoundError):
        app.run_additional_agents(
            args, "retry", [1], root, workspace, retry_indices={1},
            progress_callback=lambda _: None,
        )
    result = AgentResult(**json.loads((root / "meta/agent_001/status.json").read_text()))
    assert result.workspace_dir == str(workspace.resolve())
    assert (workspace / "between.txt").read_text() == "do not roll back"
    assert (workspace / "reply.txt").read_text() == "first"
    run = ManagedRun(
        run_id="source-qa", prompt="first", workspace=str(workspace.resolve()),
        run_root=str(root), artifact_token="owned-qa",
        config={"run_base": str(root.parent), "workspace_mode": "source"},
        results={1: asdict(result)},
    )
    artifacts = ArtifactStore()
    artifacts.write_marker(run)
    patch_path = artifacts.persist_diff(run, 1, result.workspace_dir)
    assert "reply.txt" in patch_path.read_text()
    with pytest.raises(ArtifactError):
        artifacts.agent_workspace(run, 2)
    promotion = Mock(return_value={"session_id": "qa-session"})
    sync = Mock(side_effect=AssertionError("must not sync source to itself"))
    finalized = finalize_candidate_workspace(
        result, candidate_workspace=workspace, workspace=workspace,
        workspaces_root=root / "workspaces", promote_session=promotion,
        sync_workspace=sync,
    )
    assert finalized.workspaces_deleted
    promotion.assert_called_once()
    sync.assert_not_called()
    assert (workspace / "between.txt").read_text() == "do not roll back"
    assert "reply.txt" in patch_path.read_text()


def test_isolated_settings_still_execute_in_a_copy(local_agent):
    args, workspace = local_agent
    args.no_sync_back = True
    args.keep_workspaces = True
    assert app.run_once(args, "isolated", progress_callback=lambda _: None, print_output=False) == 0
    assert not (workspace / "reply.txt").exists()
    root = next(Path(args.runs_dir).iterdir())
    assert (root / "workspaces" / "agent_001" / "reply.txt").read_text() == "isolated"


def test_plugin_defaults_to_source_and_preserves_review_after_adoption(local_agent, tmp_path):
    args, workspace = local_agent
    manager = PluginRunManager(tmp_path / "state", detached_workers=False)
    try:
        started = manager.start_run(
            "plugin", str(workspace), num_agents=1, synthesis_agents=0,
            runs_dir=args.runs_dir, confirm_large_run=True,
        )
        completed = manager.wait_for_run(started["run_id"], timeout_seconds=10)
        assert completed["status"] == "completed"
        assert completed["config"]["workspace_mode"] == "source"
        assert completed["agents"][0]["workspace_dir"] == str(workspace.resolve())
        assert not completed["can_add_agents"]
        with pytest.raises(PluginRunError, match="source folder"):
            manager.add_agents(started["run_id"], 1)
        finalized = manager.accept_agent(started["run_id"], 1)
        assert finalized["status"] == "finalized"
        assert finalized["workspaces_deleted"]
        assert (workspace / "reply.txt").read_text() == "plugin"
        root = Path(finalized["run_root"])
        assert "reply.txt" in (root / "plugin" / "diffs" / "agent_001.patch").read_text()
    finally:
        manager.close()


def test_cleanup_and_concurrent_sync_cannot_damage_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.txt").write_text("keep")
    for cleanup in (cleanup_workspace_copy, cleanup_workspace_copies):
        for path in (source, tmp_path):
            with pytest.raises(RuntimeError, match="source workspace"):
                cleanup(source, path)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    with source_workspace_lease(source):
        with pytest.raises(RuntimeError, match="already performing"):
            sync_best_workspace_back(candidate, source)
    assert (source / "keep.txt").read_text() == "keep"
