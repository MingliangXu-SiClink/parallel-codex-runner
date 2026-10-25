import asyncio
import json
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from parallel_codex_runner_core import app


def wait_for_fixture_file(path):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def local_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("PCR_CODEX_TRANSPORT", "exec")
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "base.txt").write_text("original\n")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setattr(app, "get_codex_home", lambda: codex_home)
    monkeypatch.setattr(app, "read_codex_exec_help", lambda *_: "Usage: codex exec --json")
    monkeypatch.setattr(app, "read_effective_codex_developer_instructions", lambda *a, **k: "shared guidance")
    monkeypatch.setattr(app.CodexModelCacheManager, "populate", lambda *_: False)
    command = [sys.executable, "-c", (
        "from pathlib import Path; import sys; "
        "Path('reply.txt').write_text(sys.stdin.read()); print('local test complete')"
    )]
    monkeypatch.setattr(app, "build_codex_command", lambda *a, **k: (command, {"json": True}))
    args = app.parse_args([
        "prompt", "-n", "5", "--synthesis-agents", "0", "--workspace", str(workspace),
        "--runs-dir", str(tmp_path / "runs"), "--no-sync-back", "--keep-workspaces",
    ])
    args.cancel_event = threading.Event()
    return args, workspace


def test_remote_access_guidance_reaches_all_candidates_and_synthesis_agents(
    local_pipeline, monkeypatch,
):
    args, workspace = local_pipeline
    args.synthesis_agents = 2
    prepared = {}
    build = app.build_codex_command

    def capture(codex, help_text, final_message, **kwargs):
        prepared[final_message.parent.name] = kwargs["developer_instructions"]
        return build(codex, help_text, final_message, **kwargs)

    monkeypatch.setattr(app, "build_codex_command", capture)
    assert app.run_once(args, "authorized remote task", progress_callback=lambda _: None, print_output=False) == 0
    root = next(Path(args.runs_dir).iterdir())
    assert set(prepared) == {f"agent_{idx:03d}" for idx in range(1, 8)}
    for agent, instructions in prepared.items():
        assert instructions.startswith("shared guidance")
        assert "PCR remote environment access:" in instructions
        assert json.dumps({"run": root.name, "agent": int(agent.removeprefix("agent_"))}) in instructions
        assert ".pcr/" not in instructions
        assert str(root / "workspaces" / agent) in instructions
    assert not (workspace / "reply.txt").exists()


@pytest.mark.parametrize("additional", [False, True], ids=["initial", "additional"])
def test_ready_agent_starts_before_other_agent_finishes_preparation(
    local_pipeline, tmp_path, monkeypatch, additional,
):
    args, workspace = local_pipeline
    slow_preparation_overlapped_execution = []
    events = []

    def copy(source, destination, **kwargs):
        if destination.name == "agent_002":
            slow_preparation_overlapped_execution.append(
                wait_for_fixture_file(destination.parent / "agent_001/reply.txt")
            )
        shutil.copytree(source, destination)

    def report(event):
        events.append(event)

    monkeypatch.setattr(app, "copy_workspace", copy)
    if additional:
        results = app.run_additional_agents(
            args, "pipeline", [1, 2], tmp_path / "existing-run", workspace,
            progress_callback=report, cancel_event=args.cancel_event,
        )
        assert all(result.status == "success" for result in results)
    else:
        assert app.run_once(args, "pipeline", progress_callback=report, print_output=False) == 0
    assert slow_preparation_overlapped_execution == [True]
    assert not (workspace / "reply.txt").exists()
    assert (workspace / "base.txt").read_text() == "original\n"
    for idx in {event["idx"] for event in events if event.get("type") == "agent_started"}:
        statuses = [event["status"] for event in events
                    if event.get("type") == "agent_status" and event.get("idx") == idx]
        assert statuses == ["queued", "copying"]


def test_slow_first_preparation_does_not_hold_back_ready_second_agent(local_pipeline, monkeypatch):
    args, _ = local_pipeline
    concurrent = []

    def copy(source, destination, **kwargs):
        if destination.name == "agent_001":
            concurrent.append(wait_for_fixture_file(destination.parent / "agent_002/reply.txt"))
        shutil.copytree(source, destination)

    monkeypatch.setattr(app, "copy_workspace", copy)
    assert app.run_once(args, "pipeline", progress_callback=lambda _: None, print_output=False) == 0
    assert concurrent == [True]


@pytest.mark.parametrize("serial,max_parallel,expected", [
    (False, 5, 5), (False, 3, 3), (False, 1, 1), (True, 5, 1),
])
def test_preparation_uses_configured_parallelism_and_respects_serial_setting(
    local_pipeline, monkeypatch, serial, max_parallel, expected,
):
    args, _ = local_pipeline
    args.serial, args.max_parallel = serial, max_parallel
    active = peak = 0
    lock = threading.Lock()
    preparations_ready = threading.Event()

    def copy(source, destination, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == expected:
                preparations_ready.set()
        try:
            assert preparations_ready.wait(3)
            shutil.copytree(source, destination)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(app, "copy_workspace", copy)
    assert app.run_once(args, "pipeline", progress_callback=lambda _: None, print_output=False) == 0
    assert peak == expected


@pytest.mark.parametrize("additional", [False, True], ids=["initial", "additional"])
def test_ready_fifth_agent_starts_while_first_four_are_preparing(
    local_pipeline, tmp_path, monkeypatch, additional,
):
    args, workspace = local_pipeline
    args.max_parallel = 5
    overlap = []

    def copy(source, destination, **kwargs):
        if destination.name != "agent_005":
            overlap.append(wait_for_fixture_file(destination.parent / "agent_005/reply.txt"))
        shutil.copytree(source, destination)

    monkeypatch.setattr(app, "copy_workspace", copy)
    if additional:
        results = app.run_additional_agents(
            args, "pipeline", [1, 2, 3, 4, 5], tmp_path / "existing-run", workspace,
            progress_callback=lambda _: None, cancel_event=args.cancel_event,
        )
        assert all(result.status == "success" for result in results)
    else:
        assert app.run_once(args, "pipeline", progress_callback=lambda _: None, print_output=False) == 0
    assert overlap == [True] * 4
    assert not (workspace / "reply.txt").exists()
    assert (workspace / "base.txt").read_text() == "original\n"


def test_one_preparation_failure_does_not_abort_other_agents(local_pipeline, monkeypatch):
    args, workspace = local_pipeline

    def copy(source, destination, **kwargs):
        if destination.name == "agent_001":
            destination.mkdir(parents=True)
            (destination / "partial.txt").touch()
            raise OSError("fixture copy unavailable")
        shutil.copytree(source, destination)

    monkeypatch.setattr(app, "copy_workspace", copy)
    assert app.run_once(args, "pipeline", progress_callback=lambda _: None, print_output=False) == 0
    root = next(Path(args.runs_dir).iterdir())
    summary = json.loads((root / "summary.json").read_text())
    statuses = {result["idx"]: result["status"] for result in summary["results"]}
    assert statuses == {1: "error", 2: "success", 3: "success", 4: "success", 5: "success"}
    assert not (root / "workspaces/agent_001").exists()
    assert summary["best_agent"] != "agent_001"
    assert (workspace / "base.txt").read_text() == "original\n"


def test_preparation_failure_can_be_retried_in_the_same_candidate_stage(local_pipeline, monkeypatch):
    args, _ = local_pipeline
    args.live_candidate_coordinator = app.LiveCandidateCoordinator()
    copies = []
    retries = []

    def copy(source, destination, **kwargs):
        copies.append(destination.name)
        if destination.name == "agent_001" and copies.count("agent_001") == 1:
            raise OSError("temporary fixture copy failure")
        if destination.name == "agent_002":
            assert wait_for_fixture_file(destination.parent / "agent_001/reply.txt")
        shutil.copytree(source, destination)

    def report(event):
        if event.get("type") == "agent_finished" and event.get("idx") == 1:
            if event["result"]["status"] == "error":
                retries.append(args.live_candidate_coordinator.request_retry(1))

    monkeypatch.setattr(app, "copy_workspace", copy)
    assert app.run_once(args, "pipeline", progress_callback=report, print_output=False) == 0
    assert retries == [True]
    assert copies.count("agent_001") == 2
    root = next(Path(args.runs_dir).iterdir())
    results = json.loads((root / "summary.json").read_text())["results"]
    assert {result["idx"]: result["status"] for result in results} == {
        idx: "success" for idx in range(1, 6)
    }
    assert (root / "retry_history/agent_001").is_dir()


def test_cancel_during_preparation_never_launches_or_prepares_queued_agents(local_pipeline, monkeypatch):
    args, _ = local_pipeline
    args.max_parallel = 1
    copies = []
    events = []

    def copy(source, destination, **kwargs):
        copies.append(destination.name)
        args.cancel_event.set()
        shutil.copytree(source, destination)

    monkeypatch.setattr(app, "copy_workspace", copy)
    assert app.run_once(args, "pipeline", progress_callback=events.append, print_output=False) == 130
    assert copies == ["agent_001"]
    assert not any(event.get("type") == "agent_started" for event in events)


@pytest.mark.parametrize("deferred", [False, True])
def test_concurrent_developer_guidance_never_shares_another_workspaces_config(monkeypatch, deferred):
    calls = []

    def read(*args):
        calls.append(args)
        time.sleep(0.05)
        return "original developer guidance for " + str(args[2])

    monkeypatch.setattr(app, "read_effective_codex_developer_instructions", read)
    resolver = app._AgentDeveloperInstructionResolver()

    async def scenario():
        return await asyncio.gather(*[
            asyncio.to_thread(resolver.for_workspace, codex_bin="fixture",
                              codex_home=Path(f"/tmp/fixture-home-{idx}"),
                              agent_workspace=Path(f"/tmp/fixture-agent-{idx}"),
                              run_root=Path("/tmp/fixture-run"), agent_idx=idx,
                              defer_resolution=deferred)
            for idx in range(1, 6)
        ])

    instructions = asyncio.run(scenario())
    assert len(calls) == (0 if deferred else 5)
    for idx, text in zip(range(1, 6), instructions):
        if deferred:
            assert "original developer guidance" not in text
        else:
            assert f"original developer guidance for /tmp/fixture-agent-{idx}" in text
        assert f"/tmp/fixture-agent-{idx}" in text
        assert f'"run": "fixture-run", "agent": {idx}' in text
        assert ".pcr/" not in text
        for other in range(1, 6):
            if other != idx:
                assert f"/tmp/fixture-agent-{other}" not in text


def test_synthesis_preparation_is_pipelined_but_waits_for_all_candidates(local_pipeline, monkeypatch):
    args, _ = local_pipeline
    args.num_agents, args.synthesis_agents = 2, 2
    candidates_finished = set()
    overlap = []

    def copy(source, destination, **kwargs):
        if destination.name == "agent_004":
            overlap.append(wait_for_fixture_file(destination.parent / "agent_003/reply.txt"))
        shutil.copytree(source, destination)

    def report(event):
        if event.get("type") == "agent_finished" and event.get("role") == "candidate":
            candidates_finished.add(event["idx"])
        if event.get("type") == "synthesis_started":
            assert candidates_finished == {1, 2}
        if event.get("type") == "agent_started" and event.get("idx") == 3:
            assert candidates_finished == {1, 2}

    monkeypatch.setattr(app, "copy_workspace", copy)
    assert app.run_once(args, "pipeline", progress_callback=report, print_output=False) == 0
    assert overlap == [True]


@pytest.mark.parametrize("repeat_cancel", [False, True], ids=["single-cancel", "repeated-cancel"])
def test_coroutine_cancellation_joins_copy_before_returning(tmp_path, repeat_cancel):
    copying = threading.Event()
    release = threading.Event()
    copied = threading.Event()
    events = []

    def prepare(idx):
        copying.set()
        assert release.wait(3)
        copied.set()
        return [sys.executable, "-c", "pass"], tmp_path / "home"

    async def scenario():
        runner = asyncio.create_task(app.run_all_agents(
            1, tmp_path / "workspaces", tmp_path / "meta", "fixture", {}, {}, 1,
            progress_callback=events.append, initial_agent_preparer=prepare,
        ))
        while not copying.is_set():
            await asyncio.sleep(0.01)
        runner.cancel()
        await asyncio.sleep(0.05)
        assert not runner.done()
        if repeat_cancel:
            runner.cancel()
            await asyncio.sleep(0.05)
            assert not runner.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert copied.is_set()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert not any(event.get("type") == "agent_started" for event in events)
