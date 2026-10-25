import datetime as dt
import inspect
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import parallel_codex_runner_core.plugin_runtime as plugin_runtime
from parallel_codex_runner_core.plugin import worker as plugin_worker
from parallel_codex_runner_core.codex_models import (
    CodexModelInfo,
    CodexModelRegistry,
)
from parallel_codex_runner_core.models import (
    AgentResult,
    CodexHistoryEntry,
    ResumeSession,
)
from parallel_codex_runner_core.plugin_runtime import (
    LARGE_RUN_STORAGE_WARNING_BYTES,
    ManagedRun,
    PluginRunError,
    PluginRunPendingError,
    PluginRunManager,
)
from parallel_codex_runner_core.plugin.artifacts import ArtifactError, RUN_MARKER_NAME
from parallel_codex_runner_core.plugin.events import EventLog
from parallel_codex_runner_core.plugin.lifecycle import WORKER_PROTOCOL_VERSION
from parallel_codex_runner_core.plugin import lifecycle as plugin_lifecycle
from parallel_codex_runner_core.plugin.state import STATE_VERSION


def make_storage_estimate(total_bytes: int) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_bytes_per_agent=max(1, total_bytes // 4),
        workspace_copies_bytes=max(1, total_bytes // 2),
        metadata_bytes_per_agent=max(1, total_bytes // 8),
        metadata_bytes=max(1, total_bytes // 4),
        total_bytes=total_bytes,
    )


def make_result(
    run_root: Path,
    idx: int,
    *,
    status: str = "success",
    tokens: int | None = None,
) -> AgentResult:
    workspace = run_root / "workspaces" / f"agent_{idx:03d}"
    meta = run_root / "meta" / f"agent_{idx:03d}"
    workspace.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "stdout.log").write_text("stdout\n", encoding="utf-8")
    (meta / "stderr.log").write_text("", encoding="utf-8")
    (meta / "final_message.md").write_text(
        f"Agent {idx} final response\n",
        encoding="utf-8",
    )
    return AgentResult(
        idx=idx,
        workspace_dir=str(workspace),
        meta_dir=str(meta),
        codex_home=str(meta / "codex_home"),
        stdout_log=str(meta / "stdout.log"),
        stderr_log=str(meta / "stderr.log"),
        final_message=str(meta / "final_message.md"),
        command=["codex", "exec"],
        returncode=0 if status == "success" else 1,
        status=status,
        seconds=float(idx),
        codex_thread_id=f"session-{idx}" if status == "success" else None,
        reasoning_tokens=tokens,
        reasoning_token_counts={64: idx},
        stdout_tail="tail",
    )


class PluginRunManagerTests(unittest.TestCase):
    def test_completion_cause_survives_failed_adoption_and_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(run_id="pcr-20261018T120000Z-1234abcd", prompt="test",
                    workspace=tmp, config={}, status="running", agent_statuses={1: "success", 2: "failed"})
                manager._apply_progress_locked(run, {"type": "run_finished"})
                self.assertEqual(run.completion_reason, "execution")
                manager._apply_progress_locked(run, {"type": "plugin_operation_started", "operation": "finalize"})
                manager._apply_progress_locked(run, {"type": "plugin_operation_failed", "operation": "finalize"})
                manager._apply_progress_locked(run, {"type": "plugin_operation_finished"})
                run = ManagedRun.from_dict(run.to_dict())
                self.assertEqual(run.completion_reason, "finalization")
                manager._apply_progress_locked(run, {"type": "finalization_recovered", "idx": 1,
                    "sync_was_applied": False})
                self.assertEqual(run.status, "completed")
                self.assertEqual(run.completion_reason, "finalization")
                manager._apply_progress_locked(run, {"type": "plugin_operation_started", "operation": "more"})
                manager._apply_progress_locked(run, {"type": "batch_finished"})
                self.assertEqual(run.completion_reason, "execution")
            finally:
                manager.close()

    def test_finalized_agent_links_follow_the_synced_original_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state_dir = root / "state"
            run_base = root / "runs"
            run_root = run_base / "20260713_120000"
            workspace = root / "Project Name"
            workspace.mkdir()
            (workspace / "README.md").write_text("accepted", encoding="utf-8")
            result = make_result(run_root, 1)
            candidate = Path(result.workspace_dir)
            Path(result.final_message).write_text(
                f"See [README]({candidate}/README.md) and "
                f"`{candidate}/README.md:7`. "
                f'Created :codex-file-citation{{path="{candidate}/README.md" '
                f'purpose="output"}}.\n',
                encoding="utf-8",
            )
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="test",
                workspace=str(workspace),
                config={"run_base": str(run_base)},
                status="finalized",
                run_root=str(run_root),
                agent_statuses={1: "success"},
                results={1: asdict(result)},
                finalized_agent=1,
                synced_back=True,
                artifact_token="artifact-token",
            )
            manager = PluginRunManager(state_dir)
            try:
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._write_run_marker_locked(run)
                detail = manager.get_agent(
                    run.run_id,
                    1,
                    include_events=False,
                    include_final_message_page=True,
                )

                self.assertIn(f"[README]({workspace}/README.md)", detail["final_message"])
                self.assertIn(f"`{workspace}/README.md:7`", detail["final_message"])
                self.assertIn(
                    f"[README.md](<{workspace.as_uri()}/README.md>)",
                    detail["final_message"],
                )
                self.assertNotIn(":codex-file-citation", detail["final_message"])
                self.assertEqual(
                    detail["final_message_page"]["text"],
                    detail["final_message"] + "\n",
                )
                self.assertNotIn(
                    str(candidate),
                    manager.rewrite_agent_visible_text(
                        run.run_id,
                        1,
                        f"Open [{candidate.name}]({candidate}/README.md)",
                    ),
                )
                with mock.patch.object(manager, "_require_run_locked", wraps=manager._require_run_locked) as lookup:
                    texts = manager.rewrite_agent_visible_texts(run.run_id, 1,
                        [f"Open [README]({candidate}/README.md)" for _ in range(256)])
                self.assertEqual(lookup.call_count, 1)
                self.assertEqual(texts, [f"Open [README]({workspace}/README.md)" for _ in range(256)])
                session = ResumeSession(
                    session_id="session-1",
                    title="Accepted task",
                    cwd=str(workspace),
                    updated_at=1,
                    rollout_path=str(root / "rollout.jsonl"),
                )
                with (
                    mock.patch.object(
                        plugin_runtime,
                        "list_resume_sessions",
                        return_value=[session],
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "subagent_resume_error",
                        return_value=None,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "load_codex_session_history",
                        return_value=[
                            CodexHistoryEntry(
                                "output",
                                f"[README]({candidate}/README.md)",
                            ),
                            CodexHistoryEntry(
                                "user",
                                f"Keep this literal: {candidate}/README.md",
                            ),
                        ],
                    ),
                ):
                    history = manager.resume_history(str(workspace), "session-1")
                self.assertIn(str(workspace), history["entries"][0]["text"])
                self.assertIn(str(candidate), history["entries"][1]["text"])
            finally:
                manager.close()

    def test_terminal_agent_status_cannot_be_downgraded_by_late_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-1234abcd",
                    prompt="test",
                    workspace=str(Path(tmp)),
                    config={},
                    status="running",
                    agent_statuses={1: "success"},
                )

                manager._apply_progress_locked(
                    run,
                    {"type": "agent_started", "idx": 1},
                )
                manager._apply_progress_locked(
                    run,
                    {"type": "agent_status", "idx": 1, "status": "running"},
                )

                self.assertEqual(run.agent_statuses[1], "success")
            finally:
                manager.close()

    def test_synthesis_refresh_starts_a_new_attempt_instead_of_reusing_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                result = make_result(root / "runs" / "one", 2, tokens=900)
                result.role = "synthesis"
                run = ManagedRun(
                    run_id="pcr-20260919T031718Z-1234abcd",
                    prompt="test",
                    workspace=tmp,
                    config={},
                    status="running",
                    agent_statuses={1: "success", 2: "success"},
                    agent_tokens={2: 900},
                    results={2: asdict(result)},
                    stale_synthesis_agents=[2],
                )
                manager._apply_progress_locked(run, {
                    "type": "synthesis_started", "indices": [2], "refresh": True,
                })
                queued = manager._agent_summary_locked(run, 2)
                self.assertEqual(queued["status"], "queued")
                self.assertIsNone(queued["seconds"])
                self.assertIsNone(queued["reasoning_tokens"])
                self.assertFalse(queued["can_continue"])
                self.assertEqual(run.agent_statuses[1], "success")
                self.assertNotIn(2, run.results)

                for status in ("copying", "running"):
                    manager._apply_progress_locked(run, {
                        "type": "agent_status", "idx": 2, "status": status,
                    })
                    self.assertEqual(manager._agent_summary_locked(run, 2)["status"], status)

                manager._apply_progress_locked(run, {"type": "agent_finished", "idx": 2,
                                                     "result": asdict(result)})
                manager._apply_progress_locked(run, {"type": "synthesis_finished", "indices": [2]})
                manager._apply_progress_locked(run, {"type": "agent_started", "idx": 2})
                self.assertEqual(manager._agent_summary_locked(run, 2)["status"], "success")
                self.assertEqual(run.stale_synthesis_agents, [])
            finally:
                manager.close()

    def test_run_completion_seals_agents_missing_a_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-1234abcd",
                    prompt="test",
                    workspace=str(Path(tmp)),
                    config={},
                    status="running",
                    agent_statuses={1: "success", 2: "running", 3: "queued"},
                )

                manager._apply_progress_locked(
                    run,
                    {"type": "run_finished", "cancelled": False},
                )

                self.assertEqual(run.status, "completed")
                self.assertEqual(
                    run.agent_statuses,
                    {1: "success", 2: "failed", 3: "failed"},
                )
            finally:
                manager.close()

    def test_run_completion_repairs_stale_nonterminal_result_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-1234abcd",
                    prompt="test",
                    workspace=str(Path(tmp)),
                    config={},
                    status="running",
                    agent_statuses={1: "running"},
                    results={1: {"status": "running", "seconds": 2.0}},
                )

                manager._apply_progress_locked(
                    run,
                    {"type": "run_finished", "cancelled": False},
                )

                self.assertEqual(run.agent_statuses[1], "failed")
                self.assertEqual(run.results[1]["status"], "failed")
                self.assertEqual(manager._agent_summary_locked(run, 1)["status"], "failed")
                self.assertEqual(
                    manager._run_overview_locked(run)["agents_finished"],
                    1,
                )
            finally:
                manager.close()

    def test_run_rejects_more_than_the_engineering_agent_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                with self.assertRaisesRegex(PluginRunError, "must not exceed 4096"):
                    manager.estimate(str(Path(tmp)), num_agents=4_097)
            finally:
                manager.close()

    def test_completion_snapshot_requires_every_agent_to_be_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-1234abcd",
                    prompt="test",
                    workspace=str(Path(tmp) / "EyeTheia"),
                    config={},
                    status="completed",
                    agent_statuses={1: "success", 2: "failed", 3: "running"},
                )
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()

                first = manager.completion_snapshot()[0]
                self.assertFalse(first["all_agents_terminal"])

                with manager._lock:
                    manager._runs[run.run_id].agent_statuses[3] = "killed"
                    manager._save_state_locked()
                second = manager.completion_snapshot()[0]

                self.assertTrue(second["all_agents_terminal"])
                self.assertEqual(second["workspace_name"], "EyeTheia")
            finally:
                manager.close()

    def test_run_overview_is_bounded_and_counts_all_terminal_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                statuses = {index: "success" for index in range(1, 4_097)}
                statuses.update({2: "stopped", 3: "canceled", 4: "discarded"})
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-1234abcd",
                    prompt="large private prompt" * 10_000,
                    workspace=str(Path(tmp) / "workspace"),
                    config={"model": "test"},
                    status="completed",
                    agent_statuses=statuses,
                )
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()

                overview = manager.get_run_overviews([run.run_id])["runs"][0]

                self.assertEqual(overview["agents_total"], 4_096)
                self.assertEqual(overview["agents_finished"], 4_096)
                self.assertEqual(overview["agents_failed"], 3)
                self.assertNotIn("agents", overview)
                self.assertNotIn("prompt", overview)
                self.assertLess(len(json.dumps(overview)), 512)
            finally:
                manager.close()

    def test_run_list_cursor_is_stable_when_newer_runs_are_inserted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                with manager._lock:
                    for index in range(1, 4):
                        run = ManagedRun(
                            run_id=f"pcr-20260713T12000{index}Z-1234abc{index}",
                            prompt=f"prompt {index}",
                            workspace=str(Path(tmp) / f"workspace-{index}"),
                            config={},
                            status="discarded",
                            created_at=f"2026-07-13T12:00:0{index}+00:00",
                        )
                        manager._runs[run.run_id] = run
                    manager._save_state_locked()

                first = manager.list_runs(limit=2)
                self.assertEqual(
                    [run["prompt"] for run in first["runs"]],
                    ["prompt 3", "prompt 2"],
                )
                self.assertTrue(first["has_more"])
                self.assertTrue(first["next_cursor"])

                with manager._lock:
                    newest = ManagedRun(
                        run_id="pcr-20260713T120004Z-1234abc4",
                        prompt="new prompt",
                        workspace=str(Path(tmp) / "workspace-4"),
                        config={},
                        status="discarded",
                        created_at="2026-07-13T12:00:04+00:00",
                    )
                    manager._runs[newest.run_id] = newest
                    manager._save_state_locked()

                second = manager.list_runs(limit=2, cursor=first["next_cursor"])
                self.assertEqual(
                    [run["prompt"] for run in second["runs"]],
                    ["prompt 1"],
                )
                self.assertFalse(second["has_more"])
            finally:
                manager.close()

    def test_run_list_cursor_supports_legacy_run_ids(self) -> None:
        boundary = ("2026-07-13T12:00:00+00:00", "legacy-run-id")
        cursor = plugin_runtime._run_cursor(*boundary)

        self.assertEqual(plugin_runtime._parse_run_cursor(cursor), boundary)

    def test_legacy_resume_run_uses_session_as_conversation_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-abcdef12",
                    prompt="follow up",
                    workspace=str(Path(tmp) / "workspace"),
                    config={"resume_session_id": "legacy-session"},
                    conversation_id="",
                    status="finalized",
                )
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()

                summary = manager.get_run(run.run_id)

                self.assertEqual(summary["conversation_id"], "legacy-session")
            finally:
                manager.close()

    def test_start_with_promoted_session_preserves_conversation_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "tracked.txt").write_text("original\n", encoding="utf-8")
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                previous = ManagedRun(
                    run_id="pcr-20260713T120000Z-abcdef12",
                    prompt="previous prompt",
                    workspace=str(workspace.resolve()),
                    config={},
                    conversation_id="conversation-root",
                    turn_index=4,
                    status="finalized",
                    finalized_agent=1,
                    synced_back=True,
                    session_promotion={"session_id": "promoted-session"},
                )
                with manager._lock:
                    manager._runs[previous.run_id] = previous
                    manager._save_state_locked()

                with (
                    self.patched_storage(),
                    mock.patch.object(
                        plugin_runtime,
                        "run_once",
                        side_effect=self.fake_successful_run,
                    ),
                ):
                    started = manager.start_run(
                        prompt="next prompt",
                        workspace=str(workspace),
                        num_agents=1,
                        runs_dir=str(root / "runs"),
                        resume_session_id="promoted-session",
                    )
                    summary = started["run"]
                    manager.wait_for_run(
                        started["run_id"],
                        timeout_seconds=5,
                        event_limit=100,
                    )

                self.assertEqual(summary["conversation_id"], "conversation-root")
                self.assertEqual(
                    summary["continued_from_run_id"],
                    previous.run_id,
                )
                self.assertEqual(summary["continued_from_agent"], 1)
                self.assertEqual(summary["turn_index"], 5)
            finally:
                manager.close()

    def test_run_list_can_be_scoped_to_one_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            workspace_a = Path(tmp) / "workspace-a"
            workspace_b = Path(tmp) / "workspace-b"
            try:
                with manager._lock:
                    for index, workspace in enumerate(
                        (workspace_a, workspace_b, workspace_a),
                        start=1,
                    ):
                        run = ManagedRun(
                            run_id=f"pcr-20260713T12000{index}Z-1234abc{index}",
                            prompt=f"prompt {index}",
                            workspace=str(workspace.resolve()),
                            config={},
                            status="discarded",
                            created_at=f"2026-07-13T12:00:0{index}+00:00",
                        )
                        manager._runs[run.run_id] = run
                    manager._save_state_locked()

                page = manager.list_runs(workspace=str(workspace_a))

                self.assertEqual(page["total"], 2)
                self.assertEqual(
                    [run["run_id"] for run in page["runs"]],
                    [
                        "pcr-20260713T120003Z-1234abc3",
                        "pcr-20260713T120001Z-1234abc1",
                    ],
                )
                self.assertFalse(page["has_more"])
            finally:
                manager.close()

    def test_plugin_agent_defaults_match_the_cli(self) -> None:
        for method in (PluginRunManager.estimate, PluginRunManager.start_run):
            with self.subTest(method=method.__name__):
                self.assertEqual(
                    inspect.signature(method).parameters["num_agents"].default,
                    4,
                )

    def test_plugin_build_args_preserve_full_candidate_and_synthesis_config(self) -> None:
        run = ManagedRun(
            run_id="pcr-20260713T120000Z-1234abcd",
            prompt="review it",
            workspace="/tmp/workspace",
            config={
                "num_agents": 4,
                "max_parallel": 3,
                "serial": False,
                "recommend_by": "reasoning_tokens",
                "runs_dir": "/tmp/runs",
                "codex_bin": "codex",
                "model": "candidate-model",
                "effort": "high",
                "fast": True,
                "synthesis_agents": 2,
                "synthesis_model": "synthesis-model",
                "synthesis_effort": "medium",
                "synthesis_fast": False,
                "follow_up_delay": 15,
                "subagents": True,
                "subagents_limit": 12,
            },
        )
        context = plugin_runtime._RunContext(
            cancel_event=threading.Event(),
            agent_cancel_events={},
        )

        args = PluginRunManager._build_args(run, context)

        self.assertTrue(args.fast)
        self.assertEqual(args.synthesis_agents, 2)
        self.assertEqual(args.synthesis_model, "synthesis-model")
        self.assertEqual(args.synthesis_effort, "medium")
        self.assertFalse(args.synthesis_fast)
        self.assertEqual(args.follow_up_delay, 15)
        self.assertTrue(args.subagents)
        self.assertEqual(args.subagents_limit, 12)

    def test_persistent_worker_namespace_preserves_the_same_run_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="review it",
                workspace=str(root),
                config={
                    "num_agents": 4,
                    "max_parallel": 3,
                    "serial": False,
                    "recommend_by": "reasoning_tokens",
                    "codex_bin": "codex",
                    "model": "candidate-model",
                    "effort": "high",
                    "fast": True,
                    "synthesis_agents": 2,
                    "synthesis_model": "synthesis-model",
                    "synthesis_effort": "medium",
                    "synthesis_fast": False,
                    "follow_up_delay": 0,
                    "subagents": True,
                    "subagents_limit": 12,
                },
            )

            args = plugin_worker._namespace(
                run,
                plugin_lifecycle.FileSignal(root / "stop"),
                {},
                4,
            )

            self.assertTrue(args.fast)
            self.assertEqual(args.synthesis_agents, 2)
            self.assertEqual(args.synthesis_model, "synthesis-model")
            self.assertEqual(args.synthesis_effort, "medium")
            self.assertFalse(args.synthesis_fast)
            self.assertEqual(args.follow_up_delay, 0)
            self.assertTrue(args.subagents)
            self.assertEqual(args.subagents_limit, 12)

    def test_file_backed_synthesis_settings_are_read_at_the_stage_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthesis.json"
            coordinator = plugin_worker.FileBackedSynthesisConfigCoordinator(
                path,
                {
                    "synthesis_agents": 2,
                    "synthesis_model": "old-model",
                    "synthesis_effort": "medium",
                    "synthesis_fast": False,
                },
            )
            plugin_lifecycle.write_json_atomic(
                path,
                {
                    "agents": 3,
                    "model": "new-model",
                    "effort": "high",
                    "fast": True,
                    "locked": False,
                },
            )

            configuration = coordinator.close_and_snapshot()

            self.assertEqual(configuration.agents, 3)
            self.assertEqual(configuration.model, "new-model")
            self.assertEqual(configuration.effort, "high")
            self.assertTrue(configuration.fast)
            self.assertTrue(plugin_lifecycle.read_json(path)["locked"])

    def test_file_backed_candidate_requests_join_the_active_first_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "candidates.json"
            plugin_lifecycle.initialize_candidate_requests(path)
            signals: dict[int, plugin_lifecycle.FileSignal] = {}
            coordinator = plugin_worker.FileBackedCandidateCoordinator(
                path,
                signals,
                root,
                "",
            )
            coordinator.activate()

            self.assertTrue(
                plugin_lifecycle.enqueue_candidate_requests(
                    path,
                    [3, 4],
                    [3],
                )
            )
            requests = coordinator.drain()

            self.assertEqual(
                [(request.idx, request.retry) for request in requests],
                [(3, True), (4, False)],
            )
            self.assertEqual(set(signals), {3, 4})
            self.assertIsNone(coordinator.close_if_idle())
            self.assertFalse(plugin_lifecycle.candidate_requests_accepting(path))
            self.assertFalse(
                plugin_lifecycle.enqueue_candidate_requests(path, [5])
            )

    def test_candidate_request_queue_ignores_malformed_existing_indices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            plugin_lifecycle.write_json_atomic(
                path,
                {
                    "accepting": True,
                    "requests": [
                        {"idx": "not-an-index", "retry": False},
                        {"idx": 2, "retry": False},
                    ],
                },
            )

            self.assertTrue(
                plugin_lifecycle.enqueue_candidate_requests(path, [3])
            )
            requests = plugin_worker.FileBackedCandidateCoordinator._requests(
                plugin_lifecycle.read_json(path)
            )
            self.assertEqual(
                [(request.idx, request.retry) for request in requests],
                [(2, False), (3, False)],
            )

    def test_detached_worker_uses_file_signals_for_dynamic_synthesis_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = plugin_worker.FileSignalMap(root, "", [1])

            dynamic = signals.setdefault(7, threading.Event())
            dynamic.set()

            self.assertIsInstance(dynamic, plugin_lifecycle.FileSignal)
            self.assertTrue((root / "kill-agent-007").is_file())
            self.assertIs(signals.get(7), dynamic)

    def test_estimate_uses_private_candidate_history_before_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            home = root / "global-home"
            home.mkdir()
            candidate_home = root / "candidate-home"
            rollout = candidate_home / "sessions" / "rollout-private-session.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_bytes(b"r" * 8192)
            manager = PluginRunManager(root / "state")
            try:
                with mock.patch.object(plugin_runtime, "get_codex_home", return_value=home):
                    baseline = manager.estimate(str(workspace), 1)
                    for synthesis_agents in (0, 2):
                        result = manager.estimate(
                            str(workspace),
                            1,
                            resume_session_id="private-session",
                            synthesis_agents=synthesis_agents,
                            resume_codex_home=candidate_home,
                        )
                        self.assertEqual(
                            result["metadata_bytes_per_agent"],
                            baseline["metadata_bytes_per_agent"]
                            + plugin_runtime.estimate_path_storage_bytes(rollout),
                        )
            finally:
                manager.close()

    def test_continue_from_agent_applies_configuration_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = PluginRunManager(root / "state")
            run = ManagedRun(
                run_id="pcr-existing",
                prompt="first question",
                workspace=str(workspace),
                status="completed",
                config={
                    "num_agents": 4,
                    "max_parallel": 4,
                    "serial": False,
                    "recommend_by": "reasoning_tokens",
                    "model": "old-model",
                    "effort": "high",
                    "fast": False,
                    "synthesis_agents": 2,
                    "synthesis_model": "old-review",
                    "synthesis_effort": "medium",
                    "synthesis_fast": False,
                    "sync_back": True,
                    "keep_workspaces": False,
                    "subagents": False,
                    "subagents_limit": 8,
                },
                agent_statuses={1: "success"},
                results={
                    1: {
                        "status": "success",
                        "codex_thread_id": "session-1",
                    }
                },
            )
            manager._runs[run.run_id] = run
            storage = {
                "confirmation_required": False,
                "total": "1 KiB",
                "fits": True,
            }
            try:
                with (
                    mock.patch.object(manager, "estimate", return_value=storage) as estimate,
                    mock.patch.object(
                        manager,
                        "_agent_meta_file",
                        return_value=root / "candidate-codex-home",
                    ),
                    mock.patch.object(
                        manager,
                        "_require_agent_workspace_for_action",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        manager,
                        "accept_agent",
                        return_value={
                            "status": "finalized",
                            "session_promotion": {"session_id": "session-1"},
                        },
                    ),
                    mock.patch.object(
                        manager,
                        "start_run",
                        return_value={"status": "started", "run_id": "pcr-next"},
                    ) as start,
                ):
                    result = manager.continue_from_agent(
                        run.run_id,
                        1,
                        "next question",
                        num_agents=6,
                        config_overrides={
                            "max_parallel": None,
                            "serial": False,
                            "recommend_by": "duration",
                            "model": "new-model",
                            "effort": "ultra",
                            "fast": True,
                            "synthesis_agents": 3,
                            "synthesis_model": "new-review",
                            "synthesis_effort": "high",
                            "synthesis_fast": None,
                            "follow_up_delay": 15,
                            "runs_dir": str(root / "new-runs"),
                            "codex_bin": "/usr/local/bin/codex",
                            "sync_back": True,
                            "keep_workspaces": True,
                            "subagents": True,
                            "subagents_limit": 12,
                        },
                    )

                self.assertEqual(result["run_id"], "pcr-next")
                self.assertEqual(
                    estimate.call_args.kwargs["resume_codex_home"],
                    root / "candidate-codex-home",
                )
                self.assertEqual(
                    result["continued_from"]["prompt"],
                    "first question",
                )
                kwargs = start.call_args.kwargs
                self.assertEqual(kwargs["conversation_id"], "session-1")
                self.assertEqual(kwargs["continued_from_run_id"], run.run_id)
                self.assertEqual(kwargs["continued_from_agent"], 1)
                self.assertEqual(kwargs["turn_index"], 1)
                self.assertEqual(kwargs["num_agents"], 6)
                self.assertIsNone(kwargs["max_parallel"])
                self.assertEqual(kwargs["recommend_by"], "duration")
                self.assertEqual(kwargs["model"], "new-model")
                self.assertEqual(kwargs["effort"], "ultra")
                self.assertTrue(kwargs["fast"])
                self.assertEqual(kwargs["synthesis_agents"], 3)
                self.assertEqual(kwargs["synthesis_model"], "new-review")
                self.assertEqual(kwargs["synthesis_effort"], "high")
                self.assertIsNone(kwargs["synthesis_fast"])
                self.assertEqual(kwargs["follow_up_delay"], 15)
                self.assertEqual(kwargs["runs_dir"], str(root / "new-runs"))
                self.assertEqual(kwargs["codex_bin"], "/usr/local/bin/codex")
                self.assertTrue(kwargs["keep_workspaces"])
                self.assertTrue(kwargs["subagents"])
                self.assertEqual(kwargs["subagents_limit"], 12)
            finally:
                manager.close()

    def test_finalized_agent_can_continue_after_candidate_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = PluginRunManager(root / "state")
            run = ManagedRun(
                run_id="pcr-finalized",
                prompt="first question",
                workspace=str(workspace.resolve()),
                conversation_id="conversation-1",
                turn_index=2,
                status="finalized",
                config={
                    "num_agents": 4,
                    "max_parallel": 4,
                    "serial": False,
                    "recommend_by": "reasoning_tokens",
                    "sync_back": True,
                    "keep_workspaces": False,
                    "subagents": False,
                    "subagents_limit": 8,
                },
                agent_statuses={1: "success"},
                results={
                    1: {
                        "status": "success",
                        "codex_thread_id": "agent-session",
                    }
                },
                finalized_agent=1,
                synced_back=True,
                workspaces_deleted=True,
                codex_homes_deleted=True,
                session_promotion={
                    "session_id": "promoted-session",
                    "workspace": str(workspace.resolve()),
                },
            )
            manager._runs[run.run_id] = run
            storage = {
                "confirmation_required": False,
                "total": "1 KiB",
                "fits": True,
            }
            try:
                summary = manager.get_run(run.run_id)
                self.assertFalse(summary["agents"][0]["workspace_available"])
                self.assertFalse(summary["agents"][0]["can_accept"])
                self.assertTrue(summary["agents"][0]["can_continue"])

                with (
                    mock.patch.object(manager, "estimate", return_value=storage),
                    mock.patch.object(manager, "accept_agent") as accept,
                    mock.patch.object(
                        manager,
                        "start_run",
                        return_value={"status": "started", "run_id": "pcr-next"},
                    ) as start,
                ):
                    result = manager.continue_from_agent(
                        run.run_id,
                        1,
                        "next question",
                    )

                accept.assert_not_called()
                kwargs = start.call_args.kwargs
                self.assertEqual(kwargs["resume_session_id"], "promoted-session")
                self.assertEqual(kwargs["conversation_id"], "conversation-1")
                self.assertEqual(kwargs["continued_from_run_id"], run.run_id)
                self.assertEqual(kwargs["continued_from_agent"], 1)
                self.assertEqual(kwargs["turn_index"], 3)
                self.assertEqual(result["continued_from"]["session_id"], "promoted-session")
            finally:
                manager.close()

    def test_continuation_reports_running_finalization_as_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = PluginRunManager(root / "state")
            run = ManagedRun(
                run_id="pcr-finalizing",
                prompt="first question",
                workspace=str(workspace),
                status="finalizing",
                config={"sync_back": True},
                agent_statuses={1: "success"},
                results={1: {"status": "success"}},
                finalized_agent=1,
            )
            manager._runs[run.run_id] = run
            try:
                with self.assertRaisesRegex(
                    PluginRunPendingError,
                    "continuation can be retried",
                ):
                    manager.continue_from_agent(
                        run.run_id,
                        1,
                        "next question",
                    )
            finally:
                manager.close()

    def test_continuation_waits_for_finalization_even_when_stop_status_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            run = ManagedRun(run_id="pcr-saving", prompt="first", workspace=tmp,
                             status="stopping", config={"sync_back": True},
                             agent_statuses={1: "success"}, results={1: {"status": "success"}},
                             finalized_agent=1, worker_operation="finalize", worker_state="busy")
            manager._runs[run.run_id] = run
            try:
                with mock.patch.object(manager, "_worker_active_locked", return_value=True), \
                     mock.patch.object(manager, "accept_agent") as adopt, \
                     mock.patch.object(manager, "start_run") as start:
                    with self.assertRaises(PluginRunPendingError):
                        manager.continue_from_agent(run.run_id, 1, "next")
                    adopt.assert_not_called()
                    start.assert_not_called()
            finally:
                manager.close()

    @contextmanager
    def patched_storage(self, total_bytes: int = 1024, free_bytes: int = 1 << 40):
        with (
            mock.patch.object(
                plugin_runtime,
                "estimate_run_storage",
                return_value=make_storage_estimate(total_bytes),
            ),
            mock.patch.object(
                plugin_runtime,
                "available_storage_bytes",
                return_value=free_bytes,
            ),
        ):
            yield

    @staticmethod
    def fake_successful_run(args, prompt, progress_callback, print_output):
        del prompt, print_output
        run_root = Path(args.runs_dir) / "20260713_120000"
        run_root.mkdir(parents=True)
        progress_callback(
            {
                "type": "run_prepared",
                "rows": [["RUNS_ROOT", str(run_root)]],
            }
        )
        source = Path(args.workspace)
        source_mode = getattr(args, "workspace_mode", "isolated") == "source"
        if source_mode:
            shutil.copytree(source, run_root / "workspaces" / "baseline")
        for idx in range(1, args.num_agents + 1):
            progress_callback({"type": "agent_status", "idx": idx, "status": "copying"})
            result = make_result(run_root, idx, tokens=idx * 100)
            if source_mode:
                Path(result.workspace_dir).rmdir()
                result.workspace_dir = str(source)
            else:
                shutil.copy2(source / "tracked.txt", Path(result.workspace_dir) / "tracked.txt")
            (Path(result.workspace_dir) / "tracked.txt").write_text(
                f"candidate {idx}\n",
                encoding="utf-8",
            )
            progress_callback({"type": "agent_started", "idx": idx})
            progress_callback(
                {
                    "type": "agent_line",
                    "idx": idx,
                    "stream": "stdout",
                    "text": f"candidate {idx} is working",
                }
            )
            progress_callback(
                {"type": "agent_finished", "idx": idx, "result": asdict(result)}
            )
        progress_callback(
            {
                "type": "run_finished",
                "run_root": str(run_root),
                "best_agent": args.num_agents,
                "success": True,
                "synced": False,
                "cancelled": False,
            }
        )
        return 0

    def start_completed_run(
        self,
        root: Path,
        manager: PluginRunManager,
        *,
        num_agents: int = 2,
        workspace_mode: str = "isolated",
    ) -> dict:
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "tracked.txt").write_text("original\n", encoding="utf-8")
        runs_dir = root / "runs"
        with (
            self.patched_storage(),
            mock.patch.object(plugin_runtime, "execution_workspace_mode", return_value=workspace_mode),
            mock.patch.object(
                plugin_runtime,
                "run_once",
                side_effect=self.fake_successful_run,
            ),
        ):
            started = manager.start_run(
                prompt="fix it",
                workspace=str(workspace),
                num_agents=num_agents,
                runs_dir=str(runs_dir),
            )
            completed = manager.wait_for_run(
                started["run_id"],
                timeout_seconds=5,
                event_limit=250,
            )
        self.assertFalse(completed["still_running"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["conversation_id"], completed["run_id"])
        self.assertIsNone(completed["continued_from_run_id"])
        self.assertEqual(completed["turn_index"], 0)
        return completed

    def test_saved_prompt_handoff_survives_long_diff_preservation_without_client_retries(self):
        from parallel_codex_runner_core.remote.controller import RemoteController
        from parallel_codex_runner_core.remote.ipc import PcrdServer
        from parallel_codex_runner_core.remote.journal import CommandJournal
        from parallel_codex_runner_core.remote.protocol import RemoteRequest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manager = PluginRunManager(root / "state", detached_workers=False)
            source = self.start_completed_run(root, manager, num_agents=1)
            manager._runs[source["run_id"]].config["keep_workspaces"] = True
            controller = RemoteController(state_dir=root / "remote", manager=manager)
            server = PcrdServer(root / "s", controller)
            server.prompt_delivery.poll_seconds = 0.01
            server.prompt_delivery.retry_seconds = 0.02
            preserving, release, responded, worked = (threading.Event() for _ in range(4))
            errors, responses = [], []
            preserve = manager._persist_completed_diffs_locked
            def delayed_preservation(run):
                preserving.set()
                if not release.wait(5):
                    raise RuntimeError("fixture preservation was not released")
                preserve(run)
            def finalize():
                try:
                    manager.accept_agent(source["run_id"], 1)
                except Exception as exc:
                    errors.append(exc)
            request = RemoteRequest(action="turns.continue", client_id="private-client",
                command_id="original-question", body={"run_id": source["run_id"], "agent": 1,
                    "prompt": "Continue only after the real save", "num_agents": 1, "confirm_large_run": True,
                    "runs_dir": str(root / "next-runs")})
            def send_once():
                responses.append(server.execute(request.to_mapping()))
                responded.set()
            def next_run(args, prompt, progress_callback, print_output):
                result = self.fake_successful_run(args, prompt, progress_callback, print_output)
                worked.set()
                return result
            finalizer = threading.Thread(target=finalize)
            sender = threading.Thread(target=send_once)
            with (self.patched_storage(),
                  mock.patch.object(manager, "_persist_completed_diffs_locked", side_effect=delayed_preservation),
                  mock.patch.object(plugin_runtime, "promote_best_codex_session_to_workspace", return_value={"session_id": "session-1"}),
                  mock.patch.object(plugin_runtime, "run_once", side_effect=next_run),
                  mock.patch.object(plugin_runtime, "validate_args"),
                  mock.patch.object(manager, "stop_run", wraps=manager.stop_run) as stop):
                server.prompt_delivery.start()
                finalizer.start()
                try:
                    self.assertTrue(preserving.wait(2))
                    current = manager.get_run(source["run_id"])
                    self.assertEqual(current["status"], "finalizing")
                    self.assertEqual(current["finalizing_agent"], 1)
                    self.assertIsNone(current["finalized_agent"])
                    sender.start()
                    self.assertTrue(responded.wait(0.5), "Receipt admission waited behind filesystem preservation")
                    self.assertEqual(responses[0]["error"]["code"], "pcr_operation_pending")
                    self.assertFalse(worked.is_set())
                    self.assertEqual(len(manager._runs), 1)
                    stop.assert_not_called()
                    # A concurrent state reload may replace the in-memory Run
                    # while preservation is outside the manager lock.
                    with manager._lock:
                        original = manager._runs[source["run_id"]]
                        manager._runs[source["run_id"]] = ManagedRun.from_dict(original.to_dict())
                        unrelated = ManagedRun("other-workspace-turn", "Another message", str(root / "other"),
                            {"submission_command": {"command_id": "another-original"}}, status="completed")
                        manager._runs[unrelated.run_id] = unrelated
                        manager._save_state_locked()
                    release.set()
                    finalizer.join(5)
                    self.assertFalse(errors, errors)
                    self.assertTrue(worked.wait(5), "No client retry should be needed to create and execute the next turn")
                    children = [run for run in manager._runs.values() if run.continued_from_run_id == source["run_id"]]
                    self.assertEqual(len(children), 1)
                    child = children[0]
                    self.assertEqual(child.config["submission_command"], CommandJournal.identity(request))
                    self.assertEqual(child.continued_from_agent, 1)
                    self.assertEqual(child.config["resume_session_id"], "session-1")
                    self.assertEqual(manager._runs[source["run_id"]].status, "finalized")
                    self.assertEqual(manager._runs[unrelated.run_id].config["submission_command"],
                                     {"command_id": "another-original"})
                    self.assertEqual((root / "workspace/tracked.txt").read_text(), "candidate 1\n")
                    stop.assert_not_called()
                finally:
                    release.set()
                    finalizer.join(5)
                    if sender.ident is not None:
                        sender.join(5)
                    server.server_close()
                    controller.close()

    def record_lost_idle_worker(self, manager, run_id, *, already_crashed=False):
        with manager._lock:
            run = manager._runs[run_id]
            run.worker_protocol = WORKER_PROTOCOL_VERSION
            run.worker_state = "crashed" if already_crashed else "idle"
            run.worker_pid = None if already_crashed else 999_999_999
            run.worker_operation = ""
            run.worker_operation_id = ""
            run.worker_error = "old worker failure" if already_crashed else ""
            manager._save_state_locked()
        checkpoint = {
            "protocol": WORKER_PROTOCOL_VERSION,
            "run_id": run_id,
            "pid": 999_999_999,
            "alive": True,
            "active": False,
            "operation": "",
            "operation_id": "",
            "finished_at": "2026-07-13T12:01:00+00:00",
            "last_operation": "initial",
            "exit_code": 0,
            "expired": False,
            "error": None,
        }
        path = manager.state_dir / "workers" / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(checkpoint), encoding="utf-8")
        return path, checkpoint

    def test_completed_result_survives_idle_worker_loss_and_manager_restart(self):
        for already_crashed in (False, True):
            with self.subTest(already_crashed=already_crashed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manager = PluginRunManager(root / "state", detached_workers=False)
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                self.record_lost_idle_worker(manager, run_id, already_crashed=already_crashed)
                manager.close()

                recovered = PluginRunManager(root / "state", detached_workers=True)
                try:
                    summary = recovered.get_run(run_id)
                    self.assertEqual(summary["status"], "completed")
                    self.assertEqual(summary["worker_state"], "stopped")
                    self.assertIsNone(summary["worker_error"])
                    self.assertFalse(summary["worker_available"])
                    self.assertFalse(summary["idle_expiry_recovery"])
                    self.assertFalse(summary["can_add_agents"])
                    self.assertTrue(summary["agents"][0]["can_accept"])
                    self.assertTrue(summary["agents"][0]["can_continue"])
                    self.assertEqual(recovered.get_run(run_id)["updated_at"], summary["updated_at"])
                    self.assertEqual((root / "workspace" / "tracked.txt").read_text(), "original\n")
                    with mock.patch.object(
                        recovered, "_accept_agent_via_worker", side_effect=AssertionError("must not replace worker")
                    ):
                        finalized = recovered.accept_agent(run_id, 1, keep_workspaces=True)
                    self.assertEqual(finalized["status"], "finalized")
                    self.assertIsNone(finalized["worker_error"])
                    self.assertEqual((root / "workspace" / "tracked.txt").read_text(), "candidate 1\n")
                finally:
                    recovered.close()

    def test_worker_exit_refreshes_durable_completion_before_declaring_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                self.record_lost_idle_worker(manager, run_id)
                with manager._lock:
                    run = manager._runs[run_id]
                    run.status = "running"
                    run.worker_state = "busy"
                    run.worker_operation = "initial"
                    run.worker_operation_id = "operation"
                    manager._save_state_locked()

                def observe_exit(_run):
                    # The initial event read saw a live operation. Completion
                    # becomes durable before the process liveness check returns.
                    manager._events.append(run_id, {"type": "run_finished", "success": True})
                    manager._events.append(run_id, {"type": "plugin_operation_finished",
                        "operation": "initial", "operation_id": "operation", "exit_code": 0})
                    manager._events.append(run_id, {"type": "plugin_worker_stopped", "exit_code": 0})
                    return False

                with mock.patch.object(manager, "_worker_alive_locked", side_effect=observe_exit), \
                     mock.patch.object(plugin_runtime, "scrub_agent_codex_homes") as scrub:
                    with manager._lock:
                        current = manager._require_run_locked(run_id)
                    self.assertEqual(current.status, "completed")
                    self.assertEqual(current.worker_state, "stopped")
                    self.assertEqual(current.worker_error, "")
                    self.assertEqual(current.error, "")
                    scrub.assert_not_called()
            finally:
                manager.close()

    def test_worker_exit_without_current_completion_keeps_the_pinned_source_guard(self):
        for stale_tail in (False, True):
            with self.subTest(stale_tail=stale_tail), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manager = PluginRunManager(root / "state", detached_workers=False)
                try:
                    completed = self.start_completed_run(root, manager, num_agents=1)
                    run_id = completed["run_id"]
                    checkpoint_path, checkpoint = self.record_lost_idle_worker(manager, run_id)
                    checkpoint.update(active=True, operation="retry", operation_id="current-operation")
                    checkpoint_path.write_text(json.dumps(checkpoint))
                    with manager._lock:
                        run = manager._runs[run_id]
                        run.status = "running"
                        run.worker_state = "busy"
                        run.worker_operation = "retry"
                        run.worker_operation_id = "current-operation"
                        manager._save_state_locked()

                    def observe_exit(_run):
                        if stale_tail:
                            manager._events.append(run_id, {"type": "run_finished", "success": True})
                            manager._events.append(run_id, {"type": "plugin_operation_finished",
                                "operation": "initial", "operation_id": "previous-operation", "exit_code": 0})
                        return False

                    with mock.patch.object(manager, "_worker_alive_locked", side_effect=observe_exit), \
                         mock.patch.object(plugin_runtime, "scrub_agent_codex_homes"), \
                         mock.patch.object(plugin_runtime, "spawn_worker") as replacement:
                        summary = manager.get_run(run_id)
                        self.assertEqual(summary["worker_state"], "crashed")
                        self.assertIn("pinned", summary["worker_error"])
                        self.assertFalse(summary["agents"][0]["can_continue"])
                        replacement.assert_not_called()
                finally:
                    manager.close()

    def test_recovered_source_result_continues_in_new_turn_without_overwriting_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1, workspace_mode="source")
                run_id = completed["run_id"]
                self.record_lost_idle_worker(manager, run_id, already_crashed=True)
                (root / "workspace" / "tracked.txt").write_text("user edit after completion\n")
                with (
                    self.patched_storage(),
                    mock.patch.object(manager, "start_run", return_value={"run_id": "next-turn"}) as start,
                    mock.patch.object(manager, "_accept_agent_via_worker", side_effect=AssertionError("dead worker")),
                ):
                    result = manager.continue_from_agent(run_id, 1, "continue working")
                self.assertEqual(result["run_id"], "next-turn")
                self.assertEqual(start.call_args.kwargs["conversation_id"], completed["conversation_id"])
                self.assertEqual(start.call_args.kwargs["continued_from_run_id"], run_id)
                self.assertEqual(start.call_args.kwargs["continued_from_agent"], 1)
                self.assertEqual(start.call_args.kwargs["turn_index"], 1)
                self.assertEqual((root / "workspace" / "tracked.txt").read_text(), "user edit after completion\n")
                finalized = manager.get_run(run_id)
                self.assertEqual(finalized["status"], "finalized")
                self.assertIsNone(finalized["worker_error"])
            finally:
                manager.close()

    def test_idle_worker_recovery_rejects_uncertain_or_incomplete_operations(self):
        cases = (
            "active", "exit_error", "missing_checkpoint", "wrong_run", "pending_request",
            "unfinished_agent", "sync_started", "wrong_protocol", "unfinished_operation",
            "worker_error", "missing_marker", "pending_candidates",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manager = PluginRunManager(root / "state", detached_workers=False)
                try:
                    completed = self.start_completed_run(root, manager, num_agents=1)
                    run_id = completed["run_id"]
                    path, checkpoint = self.record_lost_idle_worker(manager, run_id)
                    if case == "active":
                        checkpoint["active"] = True
                    elif case == "exit_error":
                        checkpoint["exit_code"] = 1
                    elif case == "wrong_run":
                        checkpoint["run_id"] = "another-run"
                    elif case == "wrong_protocol":
                        checkpoint["protocol"] = 999
                    elif case == "unfinished_operation":
                        checkpoint["operation"] = "finalize"
                    elif case == "worker_error":
                        checkpoint["error"] = "failed to save result"
                    path.write_text(json.dumps(checkpoint))
                    if case == "missing_checkpoint":
                        path.unlink()
                    elif case == "pending_request":
                        requests = manager.state_dir / "workers" / "requests"
                        requests.mkdir(parents=True, exist_ok=True)
                        (requests / f"{run_id}-pending.json").write_text("{}")
                    elif case == "pending_candidates":
                        pending = plugin_runtime.worker_candidate_requests_path(manager.state_dir, run_id)
                        pending.parent.mkdir(parents=True, exist_ok=True)
                        pending.write_text(json.dumps({"requests": [{"idx": 2}]}))
                    elif case == "unfinished_agent":
                        with manager._lock:
                            manager._runs[run_id].agent_statuses[2] = "queued"
                            manager._save_state_locked()
                    elif case == "sync_started":
                        manager._write_finalization_journal(manager._runs[run_id], phase="sync_started", agent=1)
                    elif case == "missing_marker":
                        Path(completed["run_root"], RUN_MARKER_NAME).unlink()

                    summary = manager.get_run(run_id)
                    self.assertFalse(summary["agents"][0]["can_accept"])
                    self.assertFalse(summary["agents"][0]["can_continue"])
                    with self.assertRaises(PluginRunError):
                        manager.accept_agent(run_id, 1)
                finally:
                    manager.close()

    def test_finalized_worker_loss_does_not_invalidate_saved_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                manager.accept_agent(run_id, 1, keep_workspaces=True)
                self.record_lost_idle_worker(manager, run_id, already_crashed=True)
                with manager._lock:
                    manager._runs[run_id].error = "unrelated error"
                    manager._save_state_locked()
                summary = manager.get_run(run_id)
                self.assertEqual(summary["status"], "finalized")
                self.assertEqual(summary["worker_state"], "stopped")
                self.assertIsNone(summary["worker_error"])
                self.assertEqual(summary["error"], "unrelated error")
            finally:
                manager.close()

    def test_discard_clears_resolved_run_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with manager._lock:
                    run = manager._runs[completed["run_id"]]
                    run.error = "old run error"
                    run.worker_error = "old worker error"

                discarded = manager.discard_run(completed["run_id"])

                self.assertEqual(discarded["status"], "discarded")
                self.assertIsNone(discarded["error"])
                self.assertIsNone(discarded["worker_error"])
            finally:
                manager.close()

    def test_storage_summary_separates_managed_and_unmanaged_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_root = Path(completed["run_root"])
                codex_home = run_root / "meta" / "agent_001" / "codex_home"
                codex_home.mkdir(parents=True)
                (codex_home / "state.bin").write_bytes(b"x" * 4096)
                unmanaged = run_root.parent / "20260713_120001"
                unmanaged.mkdir()
                (unmanaged / "output.bin").write_bytes(b"y" * 4096)

                summary = manager.storage_summary(limit=10)

                self.assertEqual(summary["total_runs"], 1)
                self.assertEqual(summary["unmanaged_count"], 1)
                self.assertGreater(summary["managed_bytes"], 0)
                self.assertGreater(summary["unmanaged_bytes"], 0)
                self.assertEqual(summary["runs"][0]["cleanup_action"], "discard")
                self.assertGreater(summary["runs"][0]["reclaimable_bytes"], 0)
            finally:
                manager.close()

    def test_storage_summary_reuses_terminal_run_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                self.start_completed_run(root, manager, num_agents=1)
                with mock.patch.object(
                    manager,
                    "_measure_run_storage",
                    wraps=manager._measure_run_storage,
                ) as measure:
                    first = manager.storage_summary(limit=10)
                    second = manager.storage_summary(limit=10)

                self.assertEqual(first["managed_bytes"], second["managed_bytes"])
                self.assertEqual(measure.call_count, 1)
            finally:
                manager.close()

    def test_storage_summary_invalidates_measurement_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.status = "finalized"
                    run.finalized_agent = 1
                    run.config["keep_workspaces"] = True
                    manager._save_state_locked()

                with mock.patch.object(
                    manager,
                    "_measure_run_storage",
                    wraps=manager._measure_run_storage,
                ) as measure:
                    manager.storage_summary(limit=10)
                    manager.cleanup_run_storage(run_id)
                    manager.storage_summary(limit=10)

                self.assertEqual(measure.call_count, 2)
            finally:
                manager.close()

    def test_storage_summary_coalesces_concurrent_terminal_scans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                self.start_completed_run(root, manager, num_agents=1)
                original = manager._measure_run_storage
                calls = 0
                calls_lock = threading.Lock()

                def slow_measure(path: Path) -> tuple[int, int, int]:
                    nonlocal calls
                    with calls_lock:
                        calls += 1
                    time.sleep(0.05)
                    return original(path)

                results: list[dict] = []
                errors: list[BaseException] = []

                def collect() -> None:
                    try:
                        results.append(manager.storage_summary(limit=10))
                    except BaseException as exc:  # pragma: no cover - assertion aid
                        errors.append(exc)

                with mock.patch.object(
                    manager,
                    "_measure_run_storage",
                    side_effect=slow_measure,
                ):
                    workers = [threading.Thread(target=collect) for _ in range(2)]
                    for worker in workers:
                        worker.start()
                    for worker in workers:
                        worker.join(timeout=2)

                self.assertFalse(errors)
                self.assertEqual(len(results), 2)
                self.assertEqual(calls, 1)
            finally:
                manager.close()

    def test_storage_summary_reuses_recent_unmanaged_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                unmanaged = Path(completed["run_root"]).parent / "20260713_120001"
                unmanaged.mkdir()
                (unmanaged / "output.bin").write_bytes(b"x" * 4096)

                with mock.patch.object(
                    plugin_runtime,
                    "estimate_path_storage_bytes",
                    wraps=plugin_runtime.estimate_path_storage_bytes,
                ) as measure:
                    manager.storage_summary(limit=10)
                    manager.storage_summary(limit=10)

                unmanaged_calls = [
                    call for call in measure.call_args_list if call.args == (unmanaged,)
                ]
                self.assertEqual(len(unmanaged_calls), 1)
            finally:
                manager.close()

    def test_storage_free_space_uses_minimum_across_distinct_filesystems(self) -> None:
        paths = {Path("/runs/one"), Path("/runs/two"), Path("/external/runs")}
        filesystem_ids = {
            "/runs/one": 10,
            "/runs/two": 10,
            "/external/runs": 20,
        }
        free_bytes = {
            "/runs/one": 900,
            "/runs/two": 850,
            "/external/runs": 400,
        }
        with (
            mock.patch.object(
                plugin_runtime,
                "_storage_filesystem_id",
                side_effect=lambda path: filesystem_ids[str(path)],
            ),
            mock.patch.object(
                plugin_runtime,
                "available_storage_bytes",
                side_effect=lambda path: free_bytes[str(path)],
            ),
        ):
            available, volume_count = plugin_runtime._storage_free_space_summary(paths)

        self.assertEqual(available, 400)
        self.assertEqual(volume_count, 2)

    def test_cleanup_run_storage_removes_retained_candidates_after_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                run_root = Path(completed["run_root"])
                codex_home = run_root / "meta" / "agent_001" / "codex_home"
                codex_home.mkdir(parents=True)
                (codex_home / "state.bin").write_bytes(b"x" * 4096)
                with manager._lock:
                    run = manager._runs[run_id]
                    run.status = "finalized"
                    run.finalized_agent = 1
                    run.config["keep_workspaces"] = True
                    manager._save_state_locked()

                before = manager.storage_summary(limit=10)
                self.assertEqual(before["runs"][0]["cleanup_action"], "cleanup")

                cleaned = manager.cleanup_run_storage(run_id)

                self.assertTrue(cleaned["workspaces_deleted"])
                self.assertTrue(cleaned["codex_homes_deleted"])
                self.assertFalse((run_root / "workspaces").exists())
                self.assertFalse(codex_home.exists())
                self.assertTrue((run_root / "plugin" / "diffs" / "agent_001.patch").exists())
                after = manager.storage_summary(limit=10)
                self.assertIsNone(after["runs"][0]["cleanup_action"])
            finally:
                manager.close()

    def test_cleanup_run_storage_refuses_unselected_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with self.assertRaisesRegex(PluginRunError, "Only finalized"):
                    manager.cleanup_run_storage(completed["run_id"])
            finally:
                manager.close()

    def execute_expired_worker(
        self,
        root: Path,
        *,
        keep_workspaces: bool,
    ) -> tuple[Path, dict]:
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "tracked.txt").write_text("original\n", encoding="utf-8")
        runs_dir = root / "runs"
        state_dir = root / "state"
        run = ManagedRun(
            run_id="pcr-20260713T120000Z-1234abcd",
            prompt="expired task",
            workspace=str(workspace.resolve()),
            config={
                "num_agents": 1,
                "max_parallel": 1,
                "serial": False,
                "recommend_by": "reasoning_tokens",
                "model": None,
                "effort": None,
                "resume_session_id": None,
                "runs_dir": str(runs_dir),
                "codex_bin": "codex",
                "keep_workspaces": keep_workspaces,
                "run_base": str(runs_dir.resolve()),
            },
            artifact_token="artifact-token",
            worker_deadline=(
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
            ).isoformat(),
        )
        request_path = state_dir / "workers" / "requests" / "request.json"
        request_path.parent.mkdir(parents=True)
        request_path.write_text(
            json.dumps(
                {
                    "protocol": WORKER_PROTOCOL_VERSION,
                    "run": run.to_dict(),
                    "operation": "initial",
                    "operation_id": "expired-operation",
                }
            ),
            encoding="utf-8",
        )
        run_root = runs_dir / "20260713_120003"

        def fake_run(args, prompt, progress_callback, print_output):
            del args, prompt, print_output
            run_root.mkdir(parents=True)
            progress_callback(
                {"type": "run_prepared", "rows": [["RUNS_ROOT", str(run_root)]]}
            )
            result = make_result(run_root, 1, tokens=100, status="cancelled")
            candidate = Path(result.workspace_dir)
            (candidate / "tracked.txt").write_text("candidate\n", encoding="utf-8")
            Path(result.codex_home).mkdir(parents=True)
            (Path(result.codex_home) / "partial-session.jsonl").write_text("partial history\n")
            progress_callback(
                {"type": "agent_finished", "idx": 1, "result": asdict(result)}
            )
            progress_callback(
                {
                    "type": "run_finished",
                    "run_root": str(run_root),
                    "cancelled": True,
                }
            )
            return 130

        with mock.patch.object(plugin_worker, "run_once", side_effect=fake_run):
            plugin_worker.execute_request(state_dir, request_path)

        events = EventLog(state_dir / "events").read(
            run.run_id,
            cursor=0,
            limit=100,
        )["events"]
        finished = next(
            event
            for event in reversed(events)
            if event["type"] == "plugin_operation_finished"
        )
        return run_root, finished

    def test_continuation_prefers_synthesis_reasoning_tokens_even_with_duration_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    for idx, tokens, seconds in [(3, 500, 200), (4, 900, 10)]:
                        result = make_result(Path(run.run_root), idx, tokens=tokens)
                        result.role = "synthesis"
                        result.seconds = seconds
                        run.results[idx] = asdict(result)
                        run.agent_statuses[idx] = "success"
                        run.agent_roles[idx] = "synthesis"
                    run.config["recommend_by"] = "duration"
                    manager._recompute_recommendation_locked(run)
                    summary = manager._run_summary_locked(run)
                    self.assertEqual(summary["recommended_agent"], 3)
                    self.assertEqual(summary["continuation_agent"], 4)
                    run.rejected_agents = [4]
                    self.assertEqual(manager._run_summary_locked(run)["continuation_agent"], 3)
                    run.stale_synthesis_agents = [3]
                    self.assertEqual(manager._run_summary_locked(run)["continuation_agent"], 2)
            finally:
                manager.close()

    def test_run_events_diff_recommendation_and_accept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager)
                run_id = completed["run_id"]
                self.assertEqual(completed["recommended_agent"], 2)
                self.assertGreater(completed["event_count"], 5)

                first_page = manager.get_events(run_id, cursor=0, limit=2)
                second_page = manager.get_events(
                    run_id,
                    cursor=first_page["next_cursor"],
                    limit=250,
                )
                self.assertEqual(len(first_page["events"]), 2)
                self.assertGreater(len(second_page["events"]), 0)
                self.assertGreater(second_page["next_cursor"], first_page["next_cursor"])

                agent = manager.get_agent(run_id, 2)
                self.assertEqual(agent["final_message"], "Agent 2 final response")
                self.assertIsNone(agent["artifact_error"])
                diff = manager.get_diff(run_id, 2, limit=12)
                self.assertTrue(diff["has_more"])
                remaining = manager.get_diff(
                    run_id,
                    2,
                    cursor=diff["next_cursor"],
                    limit=100_000,
                )
                self.assertIn("candidate 2", diff["text"] + remaining["text"])

                rejected = manager.reject_agent(run_id, 2)
                self.assertEqual(rejected["recommended_agent"], 1)

                def remove_candidates(_workspace: Path, workspaces_root: Path) -> None:
                    shutil.rmtree(workspaces_root)

                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                    mock.patch.object(
                        plugin_runtime,
                        "promote_best_codex_session_to_workspace",
                        return_value=None,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "cleanup_workspace_copies",
                        side_effect=remove_candidates,
                    ),
                ):
                    finalized = manager.accept_agent(run_id, 1)
                self.assertEqual(finalized["status"], "finalized")
                self.assertEqual(finalized["finalized_agent"], 1)
                self.assertTrue(finalized["synced_back"])
                self.assertTrue(finalized["workspaces_deleted"])
                self.assertTrue(finalized["codex_homes_deleted"])
                sync.assert_called_once()
                selected = Path(finalized["run_root"]) / "SELECTED_AGENT.txt"
                self.assertEqual(selected.read_text(encoding="utf-8"), "agent_001\n")
                persisted = manager.get_diff(run_id, 1, limit=100_000)
                self.assertIn("candidate 1", persisted["text"])
            finally:
                manager.close()

    def test_agent_final_message_is_returned_in_stable_utf8_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                message = ("中文回答\n" * 2_000) + ("tail\n" * 2_000)
                message_path = (
                    Path(completed["run_root"])
                    / "meta"
                    / "agent_001"
                    / "final_message.md"
                )
                message_path.write_text(message, encoding="utf-8")

                cursor = 0
                message_id = ""
                fragments: list[str] = []
                while True:
                    detail = manager.get_agent(
                        completed["run_id"],
                        1,
                        include_events=False,
                        include_final_message=False,
                        include_final_message_page=True,
                        final_message_cursor=cursor,
                        final_message_max_bytes=4_096,
                    )
                    self.assertEqual(detail["final_message"], "")
                    page = detail["final_message_page"]
                    self.assertLessEqual(
                        len(page["text"].encode("utf-8")),
                        4_096,
                    )
                    if message_id:
                        self.assertEqual(page["message_id"], message_id)
                    message_id = page["message_id"]
                    self.assertEqual(page["cursor"], cursor)
                    fragments.append(page["text"])
                    self.assertGreaterEqual(page["next_cursor"], cursor)
                    cursor = page["next_cursor"]
                    if not page["has_more"]:
                        break

                self.assertEqual("".join(fragments), message)
                self.assertEqual(cursor, len(message.encode("utf-8")))
            finally:
                manager.close()

    def test_agent_final_message_restarts_after_response_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final_message.md"
            path.write_text("first response\n" * 1_000, encoding="utf-8")
            first = plugin_runtime._read_utf8_text_page(
                path,
                cursor=0,
                max_bytes=4_096,
            )
            self.assertTrue(first["has_more"])

            replacement = "replacement response\n"
            path.write_text(replacement, encoding="utf-8")
            restarted = plugin_runtime._read_utf8_text_page(
                path,
                cursor=first["next_cursor"],
                max_bytes=4_096,
            )

            self.assertEqual(restarted["cursor"], 0)
            self.assertEqual(restarted["text"], replacement)
            self.assertFalse(restarted["has_more"])
            self.assertNotEqual(restarted["message_id"], first["message_id"])

    def test_agent_final_message_retries_a_concurrent_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final_message.md"
            message = "stable response\n"
            path.write_text(message, encoding="utf-8")
            stable = path.stat()
            changed = SimpleNamespace(
                st_dev=stable.st_dev,
                st_ino=stable.st_ino,
                st_size=stable.st_size + 1,
                st_mtime_ns=stable.st_mtime_ns + 1,
            )
            with mock.patch.object(
                plugin_runtime.os,
                "fstat",
                side_effect=(stable, changed, stable, stable),
            ):
                page = plugin_runtime._read_utf8_text_page(
                    path,
                    cursor=0,
                    max_bytes=4_096,
                )

            self.assertEqual(page["text"], message)
            self.assertFalse(page["has_more"])

    def test_agent_final_message_concurrent_write_is_not_an_agent_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final_message.md"
            path.write_text("changing\n", encoding="utf-8")
            stable = path.stat()
            changed = SimpleNamespace(
                st_dev=stable.st_dev,
                st_ino=stable.st_ino,
                st_size=stable.st_size + 1,
                st_mtime_ns=stable.st_mtime_ns + 1,
            )
            identities = []
            for _ in range(plugin_runtime.FINAL_MESSAGE_READ_ATTEMPTS):
                identities.extend((stable, changed))
            with mock.patch.object(
                plugin_runtime.os,
                "fstat",
                side_effect=identities,
            ):
                page = plugin_runtime._read_utf8_text_page(
                    path,
                    cursor=0,
                    max_bytes=4_096,
                )

            self.assertEqual(page["text"], "")
            self.assertEqual(page["cursor"], 0)
            self.assertTrue(page["has_more"])

    def test_accept_uses_latest_desktop_sync_and_retention_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with (
                    mock.patch.object(
                        plugin_runtime,
                        "sync_best_workspace_back",
                    ) as sync,
                    mock.patch.object(
                        plugin_runtime,
                        "cleanup_workspace_copies",
                    ) as cleanup,
                ):
                    finalized = manager.accept_agent(
                        run_id,
                        1,
                        keep_workspaces=True,
                        sync_back=False,
                    )

                self.assertEqual(finalized["status"], "finalized")
                self.assertFalse(finalized["synced_back"])
                self.assertFalse(finalized["workspaces_deleted"])
                self.assertEqual(finalized["config"]["sync_back"], False)
                self.assertEqual(finalized["config"]["keep_workspaces"], True)
                sync.assert_not_called()
                cleanup.assert_not_called()
            finally:
                manager.close()

    def test_cleaned_run_stays_readable_but_is_not_continuable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    manager._cleanup_run_artifacts_locked(
                        run,
                        preserve_diffs=True,
                    )
                    run.status = "expired"
                    manager._save_state_locked()

                summary = manager.get_run(run_id)
                agent = summary["agents"][0]
                self.assertTrue(summary["artifacts_available"])
                self.assertFalse(summary["candidate_workspaces_available"])
                self.assertFalse(summary["can_add_agents"])
                self.assertFalse(agent["workspace_available"])
                self.assertFalse(agent["can_accept"])
                self.assertFalse(agent["can_continue"])

                detail = manager.get_agent(run_id, 1)
                self.assertEqual(detail["final_message"], "Agent 1 final response")
                self.assertIsNone(detail["artifact_error"])

                with self.assertRaisesRegex(
                    PluginRunError,
                    "working copy has already been cleaned up",
                ):
                    manager.continue_from_agent(run_id, 1, "next question")
            finally:
                manager.close()

    def test_idle_expired_worker_can_finalize_its_retained_successful_agent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            manager = PluginRunManager(state_dir, detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "stopped"
                    run.worker_pid = None
                    manager._save_state_locked()
                worker_status = state_dir / "workers" / f"{run_id}.json"
                worker_status.parent.mkdir(parents=True, exist_ok=True)
                worker_status.write_text(
                    json.dumps(
                        {
                            "alive": False,
                            "active": False,
                            "stop_reason": "expired",
                            "expired": False,
                            "exit_code": 0,
                        }
                    ),
                    encoding="utf-8",
                )

                summary = manager.get_run(run_id)
                self.assertFalse(summary["worker_available"])
                self.assertTrue(summary["idle_expiry_recovery"])
                self.assertTrue(summary["agents"][0]["can_accept"])
                self.assertTrue(summary["agents"][0]["can_continue"])

                with mock.patch.object(
                    manager,
                    "_accept_agent_via_worker",
                    side_effect=AssertionError("expired idle worker must not be used"),
                ):
                    finalized = manager.accept_agent(
                        run_id,
                        1,
                        keep_workspaces=True,
                        sync_back=False,
                    )

                self.assertEqual(finalized["status"], "finalized")
                self.assertEqual(finalized["finalized_agent"], 1)
            finally:
                manager.close()

    def test_crashed_worker_is_not_treated_as_idle_expiry_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            manager = PluginRunManager(state_dir, detached_workers=False)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "stopped"
                    run.worker_pid = None
                    manager._save_state_locked()
                worker_status = state_dir / "workers" / f"{run_id}.json"
                worker_status.parent.mkdir(parents=True, exist_ok=True)
                worker_status.write_text(
                    json.dumps(
                        {
                            "alive": False,
                            "active": False,
                            "stop_reason": "crashed",
                            "expired": False,
                            "exit_code": 1,
                        }
                    ),
                    encoding="utf-8",
                )

                summary = manager.get_run(run_id)
                self.assertFalse(summary["idle_expiry_recovery"])
                self.assertFalse(summary["agents"][0]["can_accept"])
                self.assertFalse(summary["agents"][0]["can_continue"])
            finally:
                manager.close()

    def test_failed_agent_can_retry_after_its_partial_workspace_was_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.results[1]["status"] = "error"
                    run.results[1]["error"] = "workspace preparation failed"
                    run.agent_statuses[1] = "error"
                    shutil.rmtree(Path(run.results[1]["workspace_dir"]))
                    manager._save_state_locked()

                summary = manager.get_run(run_id)
                self.assertFalse(summary["agents"][0]["workspace_available"])
                self.assertTrue(summary["agents"][0]["can_retry"])
                detail = manager.get_agent(run_id, 1)
                self.assertTrue(detail["can_retry"])
            finally:
                manager.close()

    def test_run_without_recorded_artifacts_degrades_to_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = PluginRunManager(root / "state")
            run = ManagedRun(
                run_id="pcr-missing-artifacts",
                prompt="old question",
                workspace=str(workspace.resolve()),
                status="completed",
                config={"sync_back": True},
                agent_statuses={1: "success"},
                results={
                    1: {
                        "status": "success",
                        "codex_thread_id": "session-1",
                    }
                },
            )
            manager._runs[run.run_id] = run
            try:
                summary = manager.get_run(run.run_id)
                self.assertFalse(summary["artifacts_available"])
                self.assertFalse(summary["agents"][0]["can_continue"])
                detail = manager.get_agent(run.run_id, 1)
                self.assertIsNone(detail["artifact_error"])

                with self.assertRaisesRegex(
                    PluginRunError,
                    "working copy is no longer available",
                ) as raised:
                    manager.continue_from_agent(
                        run.run_id,
                        1,
                        "new question",
                    )
                self.assertNotIn("artifacts are not available", str(raised.exception))
            finally:
                manager.close()

    def test_large_run_requires_confirmation_and_low_space_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = PluginRunManager(root / "state")
            try:
                estimated = LARGE_RUN_STORAGE_WARNING_BYTES + 1
                with self.patched_storage(estimated, estimated + 1024):
                    response = manager.start_run(
                        prompt="large task",
                        workspace=str(workspace),
                        runs_dir=str(root / "runs"),
                    )
                self.assertEqual(response["status"], "confirmation_required")
                self.assertEqual(manager.list_runs()["total"], 0)

                with self.patched_storage(1024, 1023):
                    with self.assertRaisesRegex(PluginRunError, "Insufficient disk space"):
                        manager.start_run(
                            prompt="too large",
                            workspace=str(workspace),
                            runs_dir=str(root / "runs"),
                        )
            finally:
                manager.close()

    def test_adding_agents_rechecks_storage_and_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                estimated = LARGE_RUN_STORAGE_WARNING_BYTES + 1
                with self.patched_storage(estimated, estimated + 1024):
                    response = manager.add_agents(run_id, 2)
                self.assertEqual(response["status"], "confirmation_required")
                self.assertEqual(response["run"]["config"]["num_agents"], 1)

                with self.patched_storage(1024, 1023):
                    with self.assertRaisesRegex(
                        PluginRunError,
                        "Insufficient disk space for additional Agents",
                    ):
                        manager.add_agents(run_id, 1)
            finally:
                manager.close()

    def test_session_promotion_failure_does_not_undo_a_successful_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)

                def remove_candidates(_workspace: Path, workspaces_root: Path) -> None:
                    shutil.rmtree(workspaces_root)

                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                    mock.patch.object(
                        plugin_runtime,
                        "promote_best_codex_session_to_workspace",
                        side_effect=RuntimeError("session database unavailable"),
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "cleanup_workspace_copies",
                        side_effect=remove_candidates,
                    ),
                ):
                    finalized = manager.accept_agent(completed["run_id"], 1)
                self.assertEqual(finalized["status"], "finalized")
                self.assertTrue(finalized["synced_back"])
                self.assertIn(
                    "session database unavailable",
                    finalized["session_promotion"]["error"],
                )
                sync.assert_called_once()
            finally:
                manager.close()

    def test_cleanup_failure_can_be_retried_without_syncing_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                attempts = 0

                def cleanup(_workspace: Path, workspaces_root: Path) -> None:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("temporary cleanup failure")
                    shutil.rmtree(workspaces_root)

                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                    mock.patch.object(
                        plugin_runtime,
                        "promote_best_codex_session_to_workspace",
                        return_value=None,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "cleanup_workspace_copies",
                        side_effect=cleanup,
                    ),
                ):
                    accepted = manager.accept_agent(completed["run_id"], 1)
                    self.assertEqual(accepted["status"], "finalized")
                    self.assertTrue(accepted["cleanup_pending"])
                    self.assertFalse(accepted["workspaces_deleted"])
                    self.assertIn("temporary cleanup failure", accepted["cleanup_error"])
                    self.assertIsNone(accepted["error"])
                    failed = manager.get_run(completed["run_id"])
                    self.assertEqual(failed["status"], "finalized")
                    self.assertEqual(failed["finalized_agent"], 1)
                    self.assertTrue(failed["synced_back"])
                    manager._retry_finalized_cleanups()
                    finalized = manager.accept_agent(completed["run_id"], 1)

                self.assertEqual(finalized["status"], "finalized")
                self.assertTrue(finalized["workspaces_deleted"])
                self.assertFalse(finalized["cleanup_pending"])
                self.assertIsNone(finalized["cleanup_error"])
                self.assertEqual(attempts, 2)
                sync.assert_called_once()
            finally:
                manager.close()

    def test_cleanup_failure_does_not_block_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                    mock.patch.object(
                        plugin_runtime, "promote_best_codex_session_to_workspace",
                        return_value={"session_id": "session-1", "error": None},
                    ),
                    mock.patch.object(
                        plugin_runtime, "cleanup_workspace_copies",
                        side_effect=OSError(66, "Directory not empty"),
                    ),
                ):
                    manager.accept_agent(completed["run_id"], 1)
                    with (
                        mock.patch.object(manager, "start_run", return_value={"run_id": "next"}) as start,
                        mock.patch.object(manager, "estimate", return_value={"confirmation_required": False}),
                    ):
                        manager.continue_from_agent(completed["run_id"], 1, "next question")
                    self.assertEqual(start.call_args.kwargs["resume_session_id"], "session-1")
                    self.assertEqual(start.call_args.kwargs["workspace"], completed["workspace"])
                    manager._retry_finalized_cleanups()
                    summary = manager.get_run(completed["run_id"])
                self.assertEqual(summary["status"], "finalized")
                self.assertTrue(summary["cleanup_pending"])
                self.assertIsNone(summary["error"])
                sync.assert_called_once()
            finally:
                manager.close()

    def test_cleanup_retries_automatically_without_another_client_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back"),
                    mock.patch.object(plugin_runtime, "promote_best_codex_session_to_workspace"),
                    mock.patch.object(plugin_runtime, "cleanup_workspace_copies", side_effect=OSError("busy")),
                ):
                    manager.accept_agent(completed["run_id"], 1)
                with mock.patch.object(plugin_runtime, "CANDIDATE_CLEANUP_RETRY_SECONDS", 0.01):
                    with manager._lock:
                        manager._detached_workers = True
                        manager._start_cleanup_worker_locked()
                        worker = manager._cleanup_thread
                    self.assertIsNotNone(worker)
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())
                summary = manager.get_run(completed["run_id"])
                self.assertEqual(summary["status"], "finalized")
                self.assertFalse(summary["cleanup_pending"])
                self.assertTrue(summary["codex_homes_deleted"])
            finally:
                manager.close()

    def test_restart_after_manual_cleanup_keeps_adoption_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            completed = self.start_completed_run(root, manager, num_agents=1)
            run_id = completed["run_id"]
            with manager._lock:
                run = manager._runs[run_id]
                manager._persist_completed_diffs_locked(run)
                run.finalized_agent = 1
                run.synced_back = True
                run.status = "cleanup_failed"
                run.error = "Directory not empty"
                run.session_promotion = {"session_id": "session-1"}
                manager._write_finalization_journal(
                    run, phase="cleanup_started", agent=1, synced_back=True,
                )
                manager._save_state_locked()
                shutil.rmtree(Path(run.run_root) / "workspaces")
            manager.close()
            recovered = PluginRunManager(root / "state")
            try:
                summary = recovered.get_run(run_id)
                self.assertEqual(summary["status"], "finalized")
                self.assertIsNone(summary["error"])
                with mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync:
                    recovered._retry_finalized_cleanups()
                sync.assert_not_called()
                summary = recovered.get_run(run_id)
                self.assertTrue(summary["workspaces_deleted"])
                self.assertTrue(summary["codex_homes_deleted"])
                self.assertFalse(summary["cleanup_pending"])
                self.assertEqual(summary["session_promotion"]["session_id"], "session-1")
            finally:
                recovered.close()

    def test_legacy_cleanup_retry_bypasses_idle_worker_and_missing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    manager._persist_completed_diffs_locked(run)
                    run.finalized_agent = 1
                    run.synced_back = True
                    run.session_promotion = {"session_id": "session-1"}
                    run.status = "cleanup_failed"
                    run.updated_at = plugin_runtime._utc_now()
                    manager._save_state_locked()
                    shutil.rmtree(Path(run.run_root) / "workspaces")
                with (
                    mock.patch.object(manager, "_uses_persistent_worker", return_value=True),
                    mock.patch.object(manager, "_worker_active_locked", return_value=False),
                    mock.patch.object(manager, "_worker_available_locked", return_value=True),
                    mock.patch.object(manager, "_detect_worker_failure_locked", return_value=False),
                    mock.patch.object(manager, "_accept_agent_via_worker") as worker,
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                ):
                    recovered = manager.accept_agent(run_id, 1)
                self.assertEqual(recovered["status"], "finalized")
                self.assertTrue(recovered["workspaces_deleted"])
                self.assertFalse(recovered["cleanup_pending"])
                worker.assert_not_called()
                sync.assert_not_called()
            finally:
                manager.close()

    def test_metadata_cleanup_failure_preserves_partial_cleanup_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back"),
                    mock.patch.object(plugin_runtime, "promote_best_codex_session_to_workspace"),
                    mock.patch.object(manager._artifacts, "remove_codex_homes", side_effect=OSError("busy metadata")),
                ):
                    accepted = manager.accept_agent(completed["run_id"], 1)
                self.assertEqual(accepted["status"], "finalized")
                self.assertTrue(accepted["workspaces_deleted"])
                self.assertFalse(accepted["codex_homes_deleted"])
                self.assertTrue(accepted["cleanup_pending"])
                manager._retry_finalized_cleanups()
                self.assertFalse(manager.get_run(completed["run_id"])["cleanup_pending"])
            finally:
                manager.close()

    def test_cleanup_retry_does_not_hold_the_manager_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            entered = threading.Event()
            release = threading.Event()
            thread = None
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back"),
                    mock.patch.object(plugin_runtime, "promote_best_codex_session_to_workspace"),
                    mock.patch.object(plugin_runtime, "cleanup_workspace_copies", side_effect=OSError("busy")),
                ):
                    manager.accept_agent(completed["run_id"], 1)

                def slow_cleanup(_workspace: Path, workspaces_root: Path) -> None:
                    entered.set()
                    self.assertTrue(release.wait(5))
                    shutil.rmtree(workspaces_root)

                with mock.patch.object(plugin_runtime, "cleanup_workspace_copies", side_effect=slow_cleanup):
                    thread = threading.Thread(target=manager._retry_finalized_cleanups)
                    thread.start()
                    self.assertTrue(entered.wait(3))
                    self.assertTrue(manager._lock.acquire(timeout=0.5))
                    try:
                        self.assertEqual(manager.get_run(completed["run_id"])["status"], "finalized")
                    finally:
                        manager._lock.release()
                    release.set()
                    thread.join(timeout=3)
                    self.assertFalse(thread.is_alive())
            finally:
                release.set()
                if thread is not None:
                    thread.join(timeout=3)
                manager.close()

    def test_kept_candidates_and_ambiguous_sync_are_not_background_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                with manager._lock:
                    run = manager._runs[completed["run_id"]]
                    run.finalized_agent = 1
                    run.status = "finalized"
                    run.config["keep_workspaces"] = True
                    manager._save_state_locked()
                with mock.patch.object(plugin_runtime, "cleanup_workspace_copies") as cleanup:
                    manager._retry_finalized_cleanups()
                    with manager._lock:
                        run.config["keep_workspaces"] = False
                        run.status = "sync_ambiguous"
                        run.updated_at = plugin_runtime._utc_now()
                        manager._save_state_locked()
                    manager._retry_finalized_cleanups()
                cleanup.assert_not_called()
                self.assertTrue((Path(run.run_root) / "workspaces").is_dir())
            finally:
                manager.close()

    def test_restart_recovers_finalizing_run_as_cleanup_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            manager = PluginRunManager(state_dir)
            completed = self.start_completed_run(root, manager, num_agents=1)
            run_id = completed["run_id"]
            with manager._lock:
                interrupted = manager._runs[run_id]
                interrupted.finalized_agent = 1
                interrupted.synced_back = True
                interrupted.status = "finalizing"
                manager._save_state_locked()
            manager.close()

            recovered = PluginRunManager(state_dir)
            try:
                summary = recovered.get_run(run_id)
                self.assertEqual(summary["status"], "cleanup_failed")
                self.assertIn("retry cleanup", summary["error"])

                def remove_candidates(_workspace: Path, workspaces_root: Path) -> None:
                    shutil.rmtree(workspaces_root)

                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                    mock.patch.object(
                        plugin_runtime,
                        "cleanup_workspace_copies",
                        side_effect=remove_candidates,
                    ),
                ):
                    finalized = recovered.accept_agent(run_id, 1)
                self.assertEqual(finalized["status"], "finalized")
                self.assertTrue(finalized["workspaces_deleted"])
                sync.assert_not_called()
            finally:
                recovered.close()

    def test_ambiguous_sync_requires_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            manager = PluginRunManager(state_dir)
            completed = self.start_completed_run(root, manager, num_agents=1)
            run_id = completed["run_id"]
            with manager._lock:
                run = manager._runs[run_id]
                run.finalized_agent = 1
                run.status = "finalizing"
                manager._write_finalization_journal(
                    run,
                    phase="sync_started",
                    agent=1,
                    synced_back=False,
                )
                manager._save_state_locked()
            manager.close()

            recovered = PluginRunManager(state_dir)
            try:
                summary = recovered.get_run(run_id)
                self.assertEqual(summary["status"], "sync_ambiguous")
                self.assertIn("pcr_recover_finalization", summary["error"])
                reset = recovered.recover_finalization(
                    run_id,
                    sync_was_applied=False,
                )
                self.assertEqual(reset["status"], "completed")
                self.assertIsNone(reset["finalized_agent"])
            finally:
                recovered.close()

    def test_confirmed_sync_recovery_restores_session_without_syncing_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.finalized_agent = 1
                    run.status = "sync_ambiguous"
                    manager._write_finalization_journal(
                        run, phase="sync_started", agent=1, synced_back=False
                    )
                    manager._save_state_locked()
                recovered = manager.recover_finalization(run_id, sync_was_applied=True)
                self.assertEqual(recovered["status"], "cleanup_failed")
                promotion = {"session_id": "session-1", "workspace": run.workspace}
                with (
                    mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync,
                    mock.patch.object(plugin_runtime, "cleanup_workspace_copies") as cleanup,
                    mock.patch.object(
                        plugin_runtime,
                        "promote_best_codex_session_to_workspace",
                        return_value=promotion,
                    ) as promote,
                ):
                    finalized = manager.accept_agent(run_id, 1, keep_workspaces=True)
                self.assertEqual(finalized["status"], "finalized")
                self.assertEqual(finalized["session_promotion"], promotion)
                self.assertTrue(finalized["synced_back"])
                self.assertFalse(finalized["workspaces_deleted"])
                sync.assert_not_called()
                cleanup.assert_not_called()
                promote.assert_called_once()
                with (mock.patch.object(manager, "start_run", return_value={"continued": True}) as start,
                      mock.patch.object(plugin_runtime, "get_codex_home", return_value=root / "isolated-codex-home")):
                    continuation = manager.continue_from_agent(run_id, 1, "continue")
                self.assertTrue(continuation["continued"])
                self.assertEqual(start.call_args.kwargs["resume_session_id"], "session-1")
            finally:
                manager.close()

    def test_recorded_save_is_verified_before_recovery_without_writing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    destination = manager._workspace_for_run(run)
                    receipt = plugin_runtime.capture_sync_receipt(destination, destination)
                    run.finalized_agent, run.status = 1, "sync_ambiguous"
                    manager._write_finalization_journal(run, phase="sync_started", agent=1,
                                                        sync_proof_id="proof")
                    plugin_runtime.write_json_atomic(
                        manager._finalization_journal_path(run).with_name("sync-proof.json"),
                        {"id": "proof", "agent": 1, "run_id": run_id, "receipt": receipt})
                    manager._save_state_locked()
                with plugin_runtime.source_workspace_lease(destination):
                    with self.assertRaisesRegex(PluginRunError, "already"):
                        manager.recover_finalization(run_id, None)
                self.assertEqual(manager.get_run(run_id)["status"], "sync_ambiguous")
                with mock.patch.object(plugin_runtime, "sync_best_workspace_back") as sync:
                    recovered = manager.recover_finalization(run_id, None)
                    self.assertTrue(recovered["synced_back"])
                    self.assertEqual(recovered["status"], "cleanup_failed")
                    sync.assert_not_called()
            finally:
                manager.close()

    def test_missing_verification_never_guesses_or_resets_an_interrupted_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.finalized_agent, run.status = 1, "sync_ambiguous"
                    manager._write_finalization_journal(run, phase="sync_started", agent=1)
                    manager._save_state_locked()
                with self.assertRaisesRegex(PluginRunError, "no verification record"):
                    manager.recover_finalization(run_id, None)
                self.assertEqual(manager.get_run(run_id)["status"], "sync_ambiguous")
            finally:
                manager.close()

    def test_finalization_excludes_discard_and_new_candidate_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                syncing = threading.Event()
                release = threading.Event()
                outcome: list[dict | BaseException] = []

                def delayed_sync(_candidate: Path, _workspace: Path) -> None:
                    syncing.set()
                    release.wait(timeout=5)

                def remove_candidates(_workspace: Path, workspaces_root: Path) -> None:
                    shutil.rmtree(workspaces_root)

                def finalize() -> None:
                    try:
                        outcome.append(manager.accept_agent(run_id, 1))
                    except BaseException as exc:
                        outcome.append(exc)

                with (
                    mock.patch.object(
                        plugin_runtime,
                        "sync_best_workspace_back",
                        side_effect=delayed_sync,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "promote_best_codex_session_to_workspace",
                        return_value=None,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "cleanup_workspace_copies",
                        side_effect=remove_candidates,
                    ),
                ):
                    thread = threading.Thread(target=finalize)
                    thread.start()
                    self.assertTrue(syncing.wait(timeout=2))
                    with self.assertRaisesRegex(
                        PluginRunError,
                        "already performing finalization",
                    ):
                        manager.discard_run(run_id)
                    with self.assertRaisesRegex(
                        PluginRunError,
                        "already performing finalization",
                    ):
                        manager.add_agents(run_id, 1)
                    release.set()
                    thread.join(timeout=2)

                self.assertFalse(thread.is_alive())
                self.assertEqual(len(outcome), 1)
                self.assertIsInstance(outcome[0], dict)
                self.assertEqual(outcome[0]["status"], "finalized")
            finally:
                manager.close()

    def test_expired_worker_retains_workspace_and_session_until_explicit_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root, finished = self.execute_expired_worker(
                Path(tmp),
                keep_workspaces=False,
            )

            candidate = run_root / "workspaces" / "agent_001" / "tracked.txt"
            self.assertEqual(candidate.read_text(encoding="utf-8"), "candidate\n")
            self.assertTrue((run_root / "meta" / "agent_001" / "codex_home").is_dir())
            self.assertEqual(
                (run_root / "meta" / "agent_001" / "codex_home" / "partial-session.jsonl").read_text(),
                "partial history\n",
            )
            self.assertTrue(finished["expired"])
            self.assertFalse(finished["cleanup_attempted"])
            self.assertFalse(finished["workspaces_deleted"])
            self.assertFalse(finished["codex_homes_deleted"])
            self.assertIsNone(finished["cleanup_error"])

    def test_expired_worker_reports_retained_artifacts_truthfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root, finished = self.execute_expired_worker(
                Path(tmp),
                keep_workspaces=True,
            )

            self.assertTrue((run_root / "workspaces" / "agent_001").is_dir())
            self.assertTrue(
                (run_root / "meta" / "agent_001" / "codex_home").is_dir()
            )
            self.assertFalse(finished["cleanup_attempted"])
            self.assertFalse(finished["workspaces_deleted"])
            self.assertFalse(finished["codex_homes_deleted"])
            self.assertIsNone(finished["cleanup_error"])

    def test_expired_worker_event_does_not_infer_cleanup_from_no_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-1234abcd",
                    prompt="retained",
                    workspace=str(Path(tmp).resolve()),
                    config={"keep_workspaces": True},
                    status="running",
                )
                manager._apply_progress_locked(
                    run,
                    {
                        "type": "plugin_worker_finished",
                        "expired": True,
                        "cleanup_error": None,
                        "workspaces_deleted": False,
                        "codex_homes_deleted": False,
                    },
                )
                self.assertEqual(run.status, "expired")
                self.assertFalse(run.workspaces_deleted)
                self.assertFalse(run.codex_homes_deleted)
            finally:
                manager.close()

    def test_worker_removes_consumed_request_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            request = state_dir / "workers" / "requests" / "operation.json"
            request.parent.mkdir(parents=True)
            request.write_text("{}\n", encoding="utf-8")

            with mock.patch.object(plugin_worker, "execute_request", return_value=0):
                exit_code = plugin_worker.main(
                    ["--state-dir", str(state_dir), "--request", str(request)]
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(request.exists())

    def test_persistent_worker_does_not_expire_while_waiting_for_user_decision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            run_id = "pcr-20260713T120000Z-1234abcd"
            request = state_dir / "workers" / "requests" / f"{run_id}-initial.json"
            request.parent.mkdir(parents=True)
            request.write_text("{}\n", encoding="utf-8")
            expired = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
            ).isoformat()

            with mock.patch.object(
                plugin_worker,
                "_execute_request",
                return_value=(0, False, expired),
            ), mock.patch.object(
                plugin_worker.time,
                "sleep",
                side_effect=lambda _seconds: plugin_lifecycle.FileSignal(
                    state_dir / "control" / run_id / "shutdown"
                ).set(),
            ):
                exit_code = plugin_worker.serve_run(state_dir, run_id)

            status = json.loads(
                (state_dir / "workers" / f"{run_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(status["stop_reason"], "shutdown")

    def test_worker_starts_outside_caller_workspace_with_own_package_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            process = mock.Mock(pid=321)
            with mock.patch.object(
                plugin_lifecycle.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                spawned, operation_id = plugin_lifecycle.spawn_worker(
                    state_dir,
                    "pcr-20260713T120000Z-1234abcd",
                    "initial",
                    {"run": {}},
                )

            self.assertIs(spawned, process)
            self.assertTrue(operation_id.startswith("pcr-20260713T120000Z-1234abcd-"))
            command = popen.call_args.args[0]
            self.assertEqual(command[0], plugin_lifecycle.sys.executable)
            self.assertIn("--run-id", command)
            self.assertNotIn("--request", command)
            self.assertEqual(popen.call_args.kwargs["cwd"], str(state_dir))
            self.assertEqual(
                popen.call_args.kwargs["env"]["PYTHONPATH"],
                str(Path(plugin_lifecycle.__file__).resolve().parents[2]),
            )

    def test_worker_restores_parent_pythonpath_before_running_agents(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PYTHONPATH": "/pcr/package",
                plugin_lifecycle.WORKER_PACKAGE_ROOT_ENV: "/pcr/package",
                plugin_lifecycle.WORKER_PARENT_PYTHONPATH_PRESENT_ENV: "1",
                plugin_lifecycle.WORKER_PARENT_PYTHONPATH_ENV: "/user/modules",
            },
            clear=True,
        ):
            plugin_worker._restore_parent_pythonpath()

            self.assertEqual(os.environ.get("PYTHONPATH"), "/user/modules")
            self.assertNotIn(plugin_lifecycle.WORKER_PACKAGE_ROOT_ENV, os.environ)

    def test_kill_agent_stops_only_requested_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            runs_dir = root / "runs"
            running = threading.Event()

            def blocking_run(args, prompt, progress_callback, print_output):
                del prompt, print_output
                run_root = runs_dir / "20260713_120001"
                run_root.mkdir(parents=True)
                progress_callback(
                    {"type": "run_prepared", "rows": [["RUNS_ROOT", str(run_root)]]}
                )
                progress_callback({"type": "agent_started", "idx": 1})
                running.set()
                args.agent_cancel_events[1].wait(timeout=5)
                result = make_result(run_root, 1, status="killed")
                progress_callback(
                    {"type": "agent_finished", "idx": 1, "result": asdict(result)}
                )
                progress_callback(
                    {
                        "type": "run_finished",
                        "run_root": str(run_root),
                        "cancelled": False,
                    }
                )
                return 2

            manager = PluginRunManager(root / "state")
            try:
                with (
                    self.patched_storage(),
                    mock.patch.object(plugin_runtime, "run_once", side_effect=blocking_run),
                ):
                    started = manager.start_run(
                        prompt="long task",
                        workspace=str(workspace),
                        num_agents=1,
                        runs_dir=str(runs_dir),
                    )
                    self.assertTrue(running.wait(timeout=2))
                    stopped = manager.kill_agent(started["run_id"], 1)
                    self.assertEqual(stopped["status"], "stopping")
                    completed = manager.wait_for_run(
                        started["run_id"], timeout_seconds=5
                    )
                self.assertEqual(completed["agents"][0]["status"], "killed")
            finally:
                manager.close()

    def test_kill_agent_rejects_queued_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="queued task",
                workspace=str(root.resolve()),
                config={},
                status="running",
                agent_statuses={1: "queued"},
            )
            try:
                manager._runs[run.run_id] = run
                with mock.patch.object(
                    manager,
                    "_worker_active_locked",
                    return_value=True,
                ):
                    with self.assertRaisesRegex(
                        PluginRunError,
                        "queued Agents start normally",
                    ):
                        manager.kill_agent(run.run_id, 1)
                self.assertEqual(run.agent_statuses[1], "queued")
                self.assertFalse(
                    (manager.control_dir / run.run_id / "kill-agent-001").exists()
                )
            finally:
                manager.close()

    def test_retry_and_add_agents_reuse_the_verified_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            runs_dir = root / "runs"

            def initial_run(args, prompt, progress_callback, print_output):
                del prompt, print_output
                run_root = runs_dir / "20260713_120002"
                run_root.mkdir(parents=True)
                progress_callback(
                    {"type": "run_prepared", "rows": [["RUNS_ROOT", str(run_root)]]}
                )
                result = make_result(run_root, 1, status="killed")
                progress_callback(
                    {"type": "agent_finished", "idx": 1, "result": asdict(result)}
                )
                progress_callback(
                    {
                        "type": "run_finished",
                        "run_root": str(run_root),
                        "cancelled": False,
                    }
                )
                return 2

            def additional_run(
                args,
                prompt,
                agent_indices,
                run_root,
                workspace,
                resume_session_id,
                retry_indices,
                progress_callback,
                cancel_event,
                agent_cancel_events,
                **_kwargs,
            ):
                del (
                    args,
                    prompt,
                    workspace,
                    resume_session_id,
                    retry_indices,
                    cancel_event,
                    agent_cancel_events,
                )
                results = []
                for idx in agent_indices:
                    result = make_result(run_root, idx, tokens=idx * 100)
                    progress_callback(
                        {"type": "agent_finished", "idx": idx, "result": asdict(result)}
                    )
                    results.append(result)
                return results

            manager = PluginRunManager(root / "state")
            try:
                with (
                    self.patched_storage(),
                    mock.patch.object(plugin_runtime, "run_once", side_effect=initial_run),
                    mock.patch.object(
                        plugin_runtime,
                        "run_additional_agents",
                        side_effect=additional_run,
                    ),
                ):
                    started = manager.start_run(
                        prompt="retry task",
                        workspace=str(workspace),
                        num_agents=1,
                        keep_workspaces=True,
                        runs_dir=str(runs_dir),
                    )
                    manager.wait_for_run(started["run_id"], timeout_seconds=5)
                    manager.retry_agent(started["run_id"], 1)
                    retried = manager.wait_for_run(
                        started["run_id"], timeout_seconds=5
                    )
                    self.assertEqual(retried["agents"][0]["status"], "success")
                    manager.add_agents(started["run_id"], 1)
                    expanded = manager.wait_for_run(
                        started["run_id"], timeout_seconds=5
                    )
                self.assertEqual(len(expanded["agents"]), 2)
                self.assertEqual(expanded["config"]["num_agents"], 2)
                self.assertEqual(expanded["recommended_agent"], 2)
            finally:
                manager.close()

    def test_tampered_marker_blocks_diff_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_root = Path(completed["run_root"])
                marker = run_root / RUN_MARKER_NAME
                marker.write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(PluginRunError, "marker"):
                    manager.get_diff(completed["run_id"], 1)
                with self.assertRaisesRegex(PluginRunError, "marker"):
                    manager.discard_run(completed["run_id"])
                self.assertTrue((run_root / "workspaces" / "agent_001").exists())
            finally:
                manager.close()

    def test_retention_purges_run_root_and_worker_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            manager = PluginRunManager(state_dir)
            observer = None
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                run_root = Path(completed["run_root"])
                observer = PluginRunManager(state_dir)
                self.assertEqual(observer.get_run(run_id)["status"], "completed")
                manager.artifact_retention_seconds = 1
                expired_at = (
                    dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
                ).isoformat()
                with manager._lock:
                    manager._runs[run_id].expires_at = expired_at
                    manager._save_state_locked()

                control_file = state_dir / "control" / run_id / "stop"
                control_file.parent.mkdir(parents=True, exist_ok=True)
                control_file.write_text("stop\n", encoding="utf-8")
                workers_dir = state_dir / "workers"
                workers_dir.mkdir(parents=True, exist_ok=True)
                status_file = workers_dir / f"{run_id}.json"
                status_file.write_text("{}\n", encoding="utf-8")
                request_file = workers_dir / "requests" / f"{run_id}-stale.json"
                request_file.parent.mkdir(parents=True, exist_ok=True)
                request_file.write_text("{}\n", encoding="utf-8")
                operation_lock = state_dir / "locks" / f"{run_id}.lock"
                operation_lock.write_text("{}\n", encoding="utf-8")

                cleaned = manager.cleanup_expired_runs()
                observer.close()

                self.assertIn(run_id, cleaned["cleaned_records"])
                self.assertFalse(run_root.exists())
                self.assertFalse(control_file.parent.exists())
                self.assertFalse(status_file.exists())
                self.assertFalse(request_file.exists())
                self.assertFalse(operation_lock.exists())
                self.assertFalse((state_dir / "events" / f"{run_id}.jsonl").exists())
                payload = json.loads(
                    (state_dir / "runs.json").read_text(encoding="utf-8")
                )
                self.assertEqual(payload["runs"], [])
            finally:
                if observer is not None:
                    observer.close()
                manager.close()

    def test_durable_history_survives_expiry_and_worker_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            manager = PluginRunManager(state_dir)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                run_root = Path(completed["run_root"])
                old_timestamp = manager._runs[run_id].updated_at
                with manager._lock:
                    manager._runs[run_id].expires_at = (
                        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
                    ).isoformat()
                    manager._save_state_locked()
                # pcrd upgrades an existing store before startup maintenance.
                durable = PluginRunManager(state_dir, retain_history=True)
                try:
                    self.assertTrue(durable.retains_history)
                    self.assertEqual(durable.get_run(run_id)["status"], "completed")
                    self.assertEqual(durable._runs[run_id].updated_at, old_timestamp)
                    self.assertEqual(durable._runs[run_id].expires_at, "")
                    # An already-open observer and a detached worker inherit
                    # the policy without being passed a constructor flag.
                    self.assertTrue(manager.retains_history)
                    manager.cleanup_expired_runs()
                    worker = PluginRunManager(state_dir, detached_workers=False)
                    try:
                        self.assertTrue(worker.retains_history)
                        self.assertEqual(worker.cleanup_expired_runs()["cleaned_records"], [])
                        self.assertEqual(worker.get_agent(run_id, 1)["agent"], 1)
                        self.assertTrue(run_root.exists())
                        self.assertTrue((run_root / "workspaces" / "agent_001").exists())
                        self.assertTrue((state_dir / "events" / f"{run_id}.jsonl").exists())
                        payload = json.loads((state_dir / "runs.json").read_text())
                        self.assertEqual(payload["runs"][0]["expires_at"], "")
                        # Explicit user cleanup remains available.
                        worker.discard_run(run_id)
                        self.assertFalse((run_root / "workspaces" / "agent_001").exists())
                        self.assertIn(run_id, worker._runs)
                    finally:
                        worker.close()
                finally:
                    durable.close()
            finally:
                manager.close()

    def test_new_durable_runs_have_no_automatic_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", retain_history=True)
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run = manager._runs[completed["run_id"]]
                self.assertEqual(run.expires_at, "")
                self.assertEqual(run.worker_deadline, "")
            finally:
                manager.close()

    def test_default_runtime_is_unlimited_and_manual_stop_still_works(self) -> None:
        for value in ("", "0", "-1", "invalid"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with mock.patch.dict(os.environ, {"PCR_PLUGIN_RUN_TTL_SECONDS": value}):
                    manager = PluginRunManager(root / "state")
                try:
                    completed = self.start_completed_run(root, manager, num_agents=1)
                    run = manager._runs[completed["run_id"]]
                    self.assertEqual(manager.run_ttl_seconds, 0)
                    self.assertEqual(run.worker_deadline, "")
                    self.assertTrue(run.expires_at)  # Artifact retention is separate.
                    signal = plugin_lifecycle.FileSignal(root / "stop", run.worker_deadline)
                    with mock.patch.object(plugin_lifecycle.time, "time", return_value=time.time() + 30 * 86400):
                        self.assertFalse(signal.is_set())
                        signal.set()
                        self.assertTrue(signal.is_set())
                        self.assertFalse(signal.deadline_elapsed)
                finally:
                    manager.close()

    def test_explicit_runtime_limit_remains_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"PCR_PLUGIN_RUN_TTL_SECONDS": "120"}):
                manager = PluginRunManager(root / "state")
            try:
                before = time.time()
                completed = self.start_completed_run(root, manager, num_agents=1)
                run = manager._runs[completed["run_id"]]
                deadline = plugin_lifecycle.deadline_timestamp(run.worker_deadline)
                self.assertEqual(manager.run_ttl_seconds, 120)
                self.assertGreaterEqual(deadline, before + 120)
                self.assertLessEqual(deadline, time.time() + 120)
                signal = plugin_lifecycle.FileSignal(root / "stop", run.worker_deadline)
                with mock.patch.object(plugin_lifecycle.time, "time", return_value=deadline + 1):
                    self.assertTrue(signal.deadline_elapsed)
                    self.assertTrue(signal.is_set())
            finally:
                manager.close()

    def test_unlimited_active_run_is_not_stopped_or_cleaned_after_retention_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"PCR_PLUGIN_RUN_TTL_SECONDS": "0"}):
                manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    manager._contexts.pop(run_id)
                    run = manager._runs[run_id]
                    run.status = "running"
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_pid = os.getpid()
                    run.worker_state = "busy"
                    run.worker_operation = "initial"
                    run.agent_statuses[1] = "running"
                    manager._save_state_locked()
                    with mock.patch.object(plugin_lifecycle.time, "time", return_value=time.time() + 30 * 86400):
                        manager._run_maintenance_locked()
                    self.assertEqual(run.status, "running")
                    self.assertFalse((manager.control_dir / run_id / "stop").exists())
                    self.assertTrue((Path(run.run_root) / "workspaces" / "agent_001").is_dir())
                    self.assertFalse(run.workspaces_deleted)
                    run.worker_pid = None
            finally:
                manager.close()

    def test_active_runs_are_recovered_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            events_dir = state_dir / "events"
            events_dir.mkdir(parents=True)
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="unfinished",
                workspace=str(Path(tmp).resolve()),
                config={},
                status="running",
                event_count=99,
                agent_statuses={1: "running"},
            )
            (state_dir / "runs.json").write_text(
                json.dumps({"version": 1, "runs": [run.to_dict()]}),
                encoding="utf-8",
            )
            event_path = events_dir / f"{run.run_id}.jsonl"
            event_path.write_bytes(
                b'{"type":"agent_started"}\n{"type":"partial"'
            )

            manager = PluginRunManager(state_dir)
            try:
                recovered = manager.get_run(run.run_id)
                self.assertEqual(recovered["status"], "interrupted")
                self.assertEqual(recovered["agents"][0]["status"], "interrupted")
                self.assertEqual(recovered["event_count"], 1)
                manager.reject_agent(run.run_id, 1)
                events = manager.get_events(run.run_id, limit=10)
                self.assertEqual(len(events["events"]), 2)
                self.assertEqual(events["events"][-1]["type"], "agent_rejected")
            finally:
                manager.close()

    def test_additive_version_four_state_is_rewritten_for_legacy_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True)
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="completed",
                workspace=str(Path(tmp).resolve()),
                config={},
                status="completed",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )
            (state_dir / "runs.json").write_text(
                json.dumps({"version": 4, "runs": [run.to_dict()]}),
                encoding="utf-8",
            )

            manager = PluginRunManager(state_dir)
            try:
                loaded = manager.get_run(run.run_id)
                self.assertEqual(loaded["conversation_id"], "conversation-1")
                persisted = json.loads(
                    (state_dir / "runs.json").read_text(encoding="utf-8")
                )
                self.assertEqual(persisted["version"], STATE_VERSION)
                self.assertEqual(STATE_VERSION, 3)
            finally:
                manager.close()

    def test_restart_refreshes_idle_persistent_worker_events_for_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True)
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="completed",
                workspace=str(Path(tmp).resolve()),
                config={},
                status="completed",
                worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION,
                worker_state="busy",
                worker_operation="initial",
                worker_operation_id="operation-1",
                worker_deadline=(
                    dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
                ).isoformat(),
            )
            (state_dir / "runs.json").write_text(
                json.dumps({"version": 3, "runs": [run.to_dict()]}),
                encoding="utf-8",
            )
            EventLog(state_dir / "events").append(
                run.run_id,
                {
                    "type": "plugin_operation_finished",
                    "operation": "initial",
                    "operation_id": "operation-1",
                    "exit_code": 0,
                    "expired": False,
                },
            )

            manager = PluginRunManager(state_dir, detached_workers=True)
            try:
                recovered = manager.get_run(run.run_id)
                self.assertFalse(recovered["worker_active"])
                self.assertTrue(recovered["worker_available"])
                self.assertEqual(recovered["worker_state"], "idle")
                self.assertIsNone(recovered["worker_operation"])
                self.assertIsNone(recovered["worker_deadline"])
            finally:
                manager.close()

    def test_maintenance_persists_completion_observed_from_worker_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            manager = PluginRunManager(state_dir, detached_workers=True)
            observer = None
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="completed elsewhere",
                workspace=str(Path(tmp).resolve()),
                config={},
                status="running",
                worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION,
                worker_state="busy",
                worker_operation="initial",
                worker_operation_id="operation-1",
                agent_statuses={1: "running"},
            )
            try:
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()
                EventLog(state_dir / "events").append(
                    run.run_id,
                    {
                        "type": "run_finished",
                        "cancelled": False,
                    },
                )

                listed = manager.list_runs(limit=10)["runs"]
                self.assertEqual(listed[0]["status"], "completed")

                persisted = json.loads(
                    (state_dir / "runs.json").read_text(encoding="utf-8")
                )
                self.assertEqual(persisted["runs"][0]["status"], "completed")

                observer = PluginRunManager(state_dir, detached_workers=True)
                self.assertEqual(observer.get_run(run.run_id)["status"], "completed")
            finally:
                if observer is not None:
                    observer.close()
                manager.close()

    def test_resume_session_listing_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sessions = [
                ResumeSession(
                    session_id=f"session-{idx}",
                    title=f"Session {idx}",
                    cwd=str(workspace),
                    updated_at=idx,
                )
                for idx in range(3)
            ]
            manager = PluginRunManager(workspace / "state")
            try:
                with mock.patch.object(
                    plugin_runtime,
                    "list_resume_sessions",
                    return_value=sessions,
                ):
                    result = manager.resume_sessions(str(workspace), limit=2)
                self.assertEqual(result["total"], 3)
                self.assertEqual(len(result["sessions"]), 2)
            finally:
                manager.close()

    def test_resume_session_listing_is_single_flight_per_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            session = ResumeSession(
                session_id="session-1",
                title="Session 1",
                cwd=str(workspace),
                updated_at=1,
            )
            manager = PluginRunManager(workspace / "state")
            started = threading.Event()
            release = threading.Event()
            results: list[dict[str, object]] = []

            def slow_list(*_args: object, **_kwargs: object) -> list[ResumeSession]:
                started.set()
                release.wait(2)
                return [session]

            try:
                with mock.patch.object(
                    plugin_runtime,
                    "list_resume_sessions",
                    side_effect=slow_list,
                ) as list_sessions:
                    threads = [
                        threading.Thread(
                            target=lambda: results.append(
                                manager.resume_sessions(str(workspace))
                            )
                        )
                        for _ in range(4)
                    ]
                    for thread in threads:
                        thread.start()
                    self.assertTrue(started.wait(1))
                    release.set()
                    for thread in threads:
                        thread.join(2)

                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertEqual(list_sessions.call_count, 1)
                self.assertEqual(len(results), 4)
                self.assertTrue(all(result["total"] == 1 for result in results))
            finally:
                release.set()
                manager.close()

    def test_resume_history_is_visible_and_paginated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            session = ResumeSession(
                session_id="session-1",
                title="Earlier task",
                cwd=str(workspace),
                updated_at=1,
                rollout_path=str(workspace / "rollout.jsonl"),
            )
            entries = [
                CodexHistoryEntry("user", "x" * 3_000),
                CodexHistoryEntry("thought", "y" * 3_000),
                CodexHistoryEntry("output", "The parser is fixed"),
            ]
            manager = PluginRunManager(workspace / "state")
            try:
                with (
                    mock.patch.object(
                        plugin_runtime,
                        "list_resume_sessions",
                        return_value=[session],
                    ) as list_sessions,
                    mock.patch.object(
                        plugin_runtime,
                        "subagent_resume_error",
                        return_value=None,
                    ) as resume_error,
                    mock.patch.object(
                        plugin_runtime,
                        "load_codex_session_history",
                        return_value=entries,
                    ) as load_history,
                ):
                    first = manager.resume_history(
                        str(workspace),
                        "session-1",
                        limit=2,
                    )
                    second = manager.resume_history(
                        str(workspace),
                        "session-1",
                        cursor=first["next_cursor"],
                        limit=2,
                    )
                    byte_limited = manager.resume_history(
                        str(workspace),
                        "session-1",
                        limit=100,
                        max_bytes=4_096,
                    )
                    conversation = manager.resume_history(
                        str(workspace),
                        "session-1",
                        limit=100,
                        conversation_items_only=True,
                    )
                self.assertTrue(first["has_more"])
                self.assertEqual(
                    [entry["category"] for entry in first["entries"]],
                    ["user", "thought"],
                )
                self.assertFalse(second["has_more"])
                self.assertEqual(second["entries"][0]["text"], "The parser is fixed")
                self.assertEqual(len(byte_limited["entries"]), 1)
                self.assertEqual(byte_limited["next_cursor"], 1)
                self.assertTrue(byte_limited["has_more"])
                self.assertLessEqual(
                    byte_limited["serialized_bytes"],
                    byte_limited["byte_limit"],
                )
                self.assertEqual(
                    [entry["category"] for entry in conversation["entries"]],
                    ["user", "output"],
                )
                self.assertEqual(conversation["source_entries"], 3)
                self.assertEqual(conversation["total_entries"], 2)
                list_sessions.assert_called_once()
                resume_error.assert_called_once()
                load_history.assert_called_once()
            finally:
                manager.close()

    def test_resume_history_splits_one_protocol_sized_unicode_entry(self) -> None:
        text = "中文输出" * 100_000
        payloads = plugin_runtime._history_payloads(
            [
                CodexHistoryEntry(
                    "output",
                    text,
                    id="history:session-1:10",
                    turn_id="turn-1",
                )
            ]
        )
        self.assertGreater(len(payloads), 1)
        self.assertEqual("".join(item["text"] for item in payloads), text)
        self.assertTrue(
            all(
                len(item["text"].encode("utf-8"))
                <= plugin_runtime.MAX_HISTORY_ENTRY_TEXT_BYTES
                for item in payloads
            )
        )
        self.assertEqual(len({item["id"] for item in payloads}), len(payloads))
        self.assertTrue(
            all(item["id"].startswith("history:session-1:10:bytes:") for item in payloads)
        )
        self.assertTrue(all(item["turn_id"] == "turn-1" for item in payloads))

    def test_run_history_uses_the_recorded_rollout_line_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            session = ResumeSession(
                session_id="session-1",
                title="Earlier task",
                cwd=str(workspace),
                updated_at=1,
                rollout_path=str(workspace / "rollout.jsonl"),
            )
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="current question",
                workspace=str(workspace),
                config={
                    "resume_session_id": "session-1",
                    "resume_rollout_line_count": 42,
                },
            )
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()
                with (
                    mock.patch.object(
                        plugin_runtime,
                        "list_resume_sessions",
                        return_value=[session],
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "subagent_resume_error",
                        return_value=None,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "load_codex_session_history",
                        return_value=[CodexHistoryEntry("user", "earlier question")],
                    ) as load_history,
                ):
                    result = manager.run_history(run.run_id)

                self.assertEqual(result["history_boundary"], "rollout_line")
                self.assertEqual(result["entries"][0]["text"], "earlier question")
                load_history.assert_called_once_with(
                    plugin_runtime.get_codex_home(),
                    "session-1",
                    session.rollout_path,
                    max_lines=42,
                )
            finally:
                manager.close()

    def test_legacy_run_history_crops_the_current_turn_by_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            session = ResumeSession(
                session_id="session-1",
                title="Earlier task",
                cwd=str(workspace),
                updated_at=1,
                rollout_path=str(workspace / "rollout.jsonl"),
            )
            run = ManagedRun(
                run_id="pcr-20260713T120000Z-1234abcd",
                prompt="current   question",
                workspace=str(workspace),
                config={"resume_session_id": "session-1"},
            )
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()
                entries = [
                    CodexHistoryEntry("user", "earlier question"),
                    CodexHistoryEntry("output", "earlier answer"),
                    CodexHistoryEntry("user", "current question"),
                    CodexHistoryEntry("output", "current answer"),
                ]
                with (
                    mock.patch.object(
                        plugin_runtime,
                        "list_resume_sessions",
                        return_value=[session],
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "subagent_resume_error",
                        return_value=None,
                    ),
                    mock.patch.object(
                        plugin_runtime,
                        "load_codex_session_history",
                        return_value=entries,
                    ) as load_history,
                ):
                    result = manager.run_history(run.run_id)

                self.assertEqual(result["history_boundary"], "legacy_prompt")
                self.assertEqual(
                    [entry["text"] for entry in result["entries"]],
                    ["earlier question", "earlier answer"],
                )
                load_history.assert_called_once_with(
                    plugin_runtime.get_codex_home(),
                    "session-1",
                    session.rollout_path,
                    max_lines=None,
                )
            finally:
                manager.close()

    def test_invalid_state_is_preserved_before_starting_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            (state_dir / "runs.json").write_text("{broken", encoding="utf-8")

            manager = PluginRunManager(state_dir)
            try:
                health = manager.health()
                self.assertEqual(health["recorded_runs"], 0)
                self.assertTrue(health["startup_warnings"])
                self.assertEqual(
                    len(list(state_dir.glob("runs.corrupt-*.json"))),
                    1,
                )
            finally:
                manager.close()

            payload = json.loads(
                (state_dir / "runs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["runs"], [])

    def test_model_options_follow_cached_model_efforts(self) -> None:
        registry = CodexModelRegistry(
            models={
                "gpt-test": CodexModelInfo(
                    slug="gpt-test",
                    default_effort="high",
                    supported_efforts=("medium", "high"),
                )
            },
            configured_model="gpt-test",
        )
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                with mock.patch.object(
                    plugin_runtime.CodexModelRegistry,
                    "load",
                    return_value=registry,
                ), mock.patch.object(
                    plugin_runtime.CodexModelCacheManager, "catalog_path",
                    return_value=Path(tmp) / "catalog.json",
                ):
                    options = manager.model_options("gpt-test", "medium")
                self.assertEqual(options["effective_model"], "gpt-test")
                self.assertEqual(options["effective_effort"], "medium")
                self.assertEqual(options["models"][0]["label"], "default (gpt-test)")
                selected = [
                    item["value"]
                    for item in options["efforts"]
                    if item["selected"]
                ]
                self.assertEqual(selected, ["medium"])
            finally:
                manager.close()

    def test_model_options_discover_new_model_and_efforts_from_the_execution_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex-home"
            home.mkdir()
            manager = PluginRunManager(Path(tmp) / "state")
            catalog = mock.Mock()
            catalog.real_codex_home = home
            manager._model_catalog = catalog

            def discover():
                snapshot = Path(tmp) / "catalog.json"
                snapshot.write_text(json.dumps({"models": [{
                    "slug": "gpt-6.1-sol", "visibility": "list",
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [{"effort": effort} for effort in (
                        "low", "medium", "high", "xhigh", "max", "ultra"
                    )],
                }]}), encoding="utf-8")
                return snapshot

            catalog.catalog_path.side_effect = discover
            try:
                with mock.patch.object(plugin_runtime, "get_codex_home", return_value=home):
                    options = manager.model_options("gpt-6.1-sol", "ultra")
                catalog.catalog_path.assert_called_once_with()
                catalog.populate.assert_not_called()
                self.assertIn("gpt-6.1-sol", [option["value"] for option in options["models"]])
                self.assertEqual(options["effective_model"], "gpt-6.1-sol")
                self.assertEqual(options["effective_effort"], "ultra")
                self.assertEqual([option["value"] for option in options["efforts"]],
                                 ["", "low", "medium", "high", "xhigh", "max", "ultra"])
            finally:
                manager.close()

    def test_model_options_do_not_follow_another_cli_versions_shared_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = (root / "codex-home").resolve()
            home.mkdir()
            shared = home / "models_cache.json"
            shared.write_text(json.dumps({
                "client_version": "0.153.4", "models": [{"slug": "gpt-5.6-sol"}],
            }), encoding="utf-8")
            catalog = plugin_runtime.CodexModelCacheManager(
                sys.executable, home, lambda *_args: None, cache_root=root / "catalog",
            )
            catalog._client_version = "0.159.0"
            snapshot = catalog._persistent_snapshot_path("0.159.0")
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({
                "client_version": "0.159.0",
                "fetched_at": "2026-01-01T00:00:00Z",
                "models": [{"slug": "gpt-6.1-sol", "supported_reasoning_levels": [
                    {"effort": "high"}, {"effort": "ultra"},
                ]}],
            }), encoding="utf-8")
            before = shared.read_bytes()
            manager = PluginRunManager(root / "state")
            manager._model_catalog = catalog
            try:
                with mock.patch.object(plugin_runtime, "get_codex_home", return_value=home), \
                     mock.patch.object(catalog, "_prewarm_locked", return_value=None):
                    options = manager.model_options()
                self.assertIn("gpt-6.1-sol", [item["value"] for item in options["models"]])
                self.assertNotIn("gpt-5.6-sol", [item["value"] for item in options["models"]])
                self.assertEqual(shared.read_bytes(), before)
            finally:
                manager.close()

    def test_missing_model_catalog_is_not_a_successful_two_option_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = (root / "codex-home").resolve()
            home.mkdir()
            catalog = plugin_runtime.CodexModelCacheManager(
                sys.executable, home, lambda *_args: None, cache_root=root / "catalog",
            )
            catalog._client_version = "0.159.0"
            manager = PluginRunManager(root / "state")
            manager._model_catalog = catalog
            try:
                with mock.patch.object(plugin_runtime, "get_codex_home", return_value=home), \
                     mock.patch.object(catalog, "_prewarm_locked", return_value=None):
                    with self.assertRaises(PluginRunError):
                        manager.model_options("gpt-6-sol")
            finally:
                manager.close()

    def test_state_directory_allows_multiple_plugin_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            first = PluginRunManager(state_dir)
            try:
                second = PluginRunManager(state_dir)
                try:
                    self.assertTrue(first.health()["ok"])
                    self.assertTrue(second.health()["ok"])
                    shared = ManagedRun(
                        run_id="pcr-20260713T120000Z-1234abcd",
                        prompt="shared",
                        workspace=str(Path(tmp).resolve()),
                        config={},
                        status="failed",
                    )
                    with first._lock:
                        first._runs[shared.run_id] = shared
                        first._save_state_locked()
                    # Mtime-cache invalidation: second sees first's newly written run
                    # because os.replace changes the file stamp even within one second.
                    self.assertEqual(second.list_runs()["total"], 1)
                    # A second rapid call must also return the same count (cache hit).
                    self.assertEqual(second.list_runs()["total"], 1)
                finally:
                    second.close()
            finally:
                first.close()

    def test_sync_skips_parse_when_file_unchanged(self) -> None:
        """Mtime-cache: list_runs on an unchanged runs.json does not re-read the file."""
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            manager = PluginRunManager(state_dir)
            try:
                run = ManagedRun(
                    run_id="pcr-20260713T120000Z-cafebabe",
                    prompt="cached",
                    workspace=str(Path(tmp).resolve()),
                    config={},
                    status="failed",
                )
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()

                load_calls: list[int] = []
                _orig_load = manager._state_store.load_payload

                def _counting_load() -> object:
                    load_calls.append(1)
                    return _orig_load()

                manager._state_store.load_payload = _counting_load  # type: ignore[method-assign]

                # Two rapid calls: second should be a cache hit (no parse).
                manager.list_runs()
                manager.list_runs()
                self.assertLessEqual(
                    len(load_calls),
                    1,
                    "Rapid successive list_runs() should not re-parse an unchanged runs.json",
                )
            finally:
                manager.close()


    @staticmethod
    def write_fake_codex(root: Path) -> Path:
        fake_codex = root / "codex"
        fake_codex.write_text(
            f"#!{sys._base_executable}\n" + """
import json
import sys
import time

def emit(value):
    print(json.dumps(value), flush=True)

if '--help' in sys.argv:
    resume = ' resume [OPTIONS] SESSION' if 'resume' in sys.argv else ' [OPTIONS]'
    print('Usage: codex exec' + resume + '\\n -c, --config KEY=VALUE\\n --json\\n --output-last-message FILE')
    sys.exit(0)
if sys.argv[1] != 'app-server':
    sys.exit(1)
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if method == 'initialize':
        result = {'userAgent': 'codex_cli_rs/0.159.2 (test)'}
    elif method == 'initialized':
        continue
    elif method == 'config/read':
        result = {'config': {'developer_instructions': None}}
    elif method in {'thread/start', 'thread/resume'}:
        result = {'thread': {'id': 'dddddddd-dddd-4ddd-dddd-dddddddddddd'}}
    elif method == 'turn/start':
        result = {'turn': {'id': 'test-turn'}}
    else:
        sys.exit(1)
    emit({'id': request['id'], 'result': result})
    if method == 'turn/start':
        time.sleep(0.6)
        emit({'method': 'item/completed', 'params': {
            'threadId': 'dddddddd-dddd-4ddd-dddd-dddddddddddd', 'turnId': 'test-turn',
            'item': {'id': 'answer', 'type': 'agentMessage',
                     'phase': 'final_answer', 'text': 'detached result'}}})
        emit({'method': 'turn/completed', 'params': {
            'threadId': 'dddddddd-dddd-4ddd-dddd-dddddddddddd',
            'turn': {'id': 'test-turn', 'status': 'completed'}}})
""",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        return fake_codex

    def test_persistent_worker_survives_manager_shutdown_and_handles_later_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "tracked.txt").write_text("original\n", encoding="utf-8")
            codex_home = root / "codex-home"
            codex_home.mkdir()
            fake_codex = self.write_fake_codex(root)
            state_dir = root / "state"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                manager = PluginRunManager(state_dir, detached_workers=True)
                response = manager.start_run(
                    prompt="slow shutdown",
                    workspace=str(workspace),
                    keep_workspaces=True,
                    runs_dir=str(root / "runs"),
                    codex_bin=str(fake_codex),
                    num_agents=1,
                )
                run_id = response["run_id"]
                pid = response["run"]["worker_pid"]
                self.assertTrue(pid)
                self.assertIsNone(response["run"]["worker_deadline"])
                manager.close(wait_seconds=0)

                recovered = PluginRunManager(state_dir, detached_workers=True)
                try:
                    completed = recovered.wait_for_run(run_id, timeout_seconds=10)
                    self.assertEqual(completed["status"], "completed", completed)
                    self.assertFalse(completed["worker_active"])
                    self.assertTrue(completed["worker_available"])
                    self.assertEqual(completed["worker_pid"], pid)
                    self.assertEqual(completed["agents"][0]["status"], "success")
                    expanded = recovered.add_agents(run_id, 1)
                    self.assertEqual(expanded["worker_pid"], pid)
                    self.assertIsNone(expanded["worker_deadline"])
                    expanded = recovered.wait_for_run(run_id, timeout_seconds=10)
                    self.assertEqual(expanded["status"], "completed")
                    self.assertEqual(len(expanded["agents"]), 2)
                    self.assertEqual(expanded["worker_pid"], pid)
                    finalized = recovered.accept_agent(
                        run_id,
                        2,
                        wait_seconds=10,
                    )
                    self.assertEqual(finalized["status"], "finalized")
                    self.assertFalse(finalized["worker_available"])
                    events = recovered.get_events(run_id, limit=250)["events"]
                    operation_pids = {
                        event.get("pid")
                        for event in events
                        if event.get("type") == "plugin_operation_started"
                    }
                    self.assertEqual(operation_pids, {pid})
                finally:
                    recovered.close()

    def test_completed_detached_run_continues_after_its_idle_process_is_killed(self):
        for keep_workspaces in (False, True):
            with self.subTest(keep_workspaces=keep_workspaces), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                workspace.mkdir()
                (workspace / "tracked.txt").write_text("original\n")
                codex_home = root / "codex-home"
                codex_home.mkdir()
                fake_codex = self.write_fake_codex(root)
                processes = []
                with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                    manager = PluginRunManager(root / "state", detached_workers=True)
                    recovered = None
                    try:
                        started = manager.start_run(
                            prompt="first turn", workspace=str(workspace), num_agents=1,
                            keep_workspaces=keep_workspaces, runs_dir=str(root / "runs"),
                            codex_bin=str(fake_codex),
                        )
                        run_id = started["run_id"]
                        process = manager._worker_processes[run_id]
                        processes.append(process)
                        completed = manager.wait_for_run(run_id, timeout_seconds=15)
                        self.assertEqual(completed["status"], "completed")
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            checkpoint = plugin_lifecycle.read_json(
                                plugin_lifecycle.worker_status_path(manager.state_dir, run_id)
                            )
                            requests = (manager.state_dir / "workers" / "requests").glob(f"{run_id}-*.json")
                            if checkpoint.get("active") is False and not any(requests):
                                break
                            time.sleep(0.05)
                        self.assertIs(checkpoint.get("active"), False)
                        result = manager._runs[run_id].results[1]
                        sessions = Path(result["codex_home"]) / "sessions"
                        sessions.mkdir(parents=True, exist_ok=True)
                        rollout = sessions / "rollout-2026-07-13T12-00-00-dddddddd-dddd-4ddd-dddd-dddddddddddd.jsonl"
                        rollout.write_text(json.dumps({
                            "type": "session_meta", "payload": {
                                "id": "dddddddd-dddd-4ddd-dddd-dddddddddddd", "cwd": result["workspace_dir"],
                                "timestamp": "2026-07-13T12:00:00Z", "source": "exec",
                            },
                        }) + "\n")
                        process.kill()
                        process.wait(timeout=5)
                        manager.close()
                        recovered = PluginRunManager(root / "state", detached_workers=True)
                        ready = recovered.get_run(run_id)
                        self.assertTrue(ready["agents"][0]["can_continue"])
                        self.assertIsNone(ready["worker_error"])
                        self.assertEqual(recovered.get_agent(run_id, 1)["final_message"], "detached result")
                        continued = recovered.continue_from_agent(run_id, 1, "second turn")
                        next_id = continued["run_id"]
                        processes.append(recovered._worker_processes[next_id])
                        finished = recovered.wait_for_run(next_id, timeout_seconds=15)
                        self.assertEqual(finished["status"], "completed", finished)
                        self.assertEqual(finished["conversation_id"], completed["conversation_id"])
                        self.assertEqual(finished["continued_from_run_id"], run_id)
                        self.assertEqual(finished["turn_index"], 1)
                        self.assertNotEqual(finished["worker_pid"], process.pid)
                        finalized = recovered.get_run(run_id)
                        self.assertEqual(finalized["status"], "finalized")
                        self.assertEqual(finalized["session_promotion"]["session_id"], "dddddddd-dddd-4ddd-dddd-dddddddddddd")
                        self.assertEqual((workspace / "tracked.txt").read_text(), "original\n")
                        recovered.accept_agent(next_id, 1, wait_seconds=10)
                    finally:
                        manager.close()
                        if recovered is not None:
                            recovered.close()
                        for process in processes:
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=5)

    def test_crashed_persistent_worker_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "idle"
                    run.worker_pid = 999_999_999
                    manager._detached_workers = True
                    manager._save_state_locked()

                summary = manager.get_run(run_id)
                self.assertEqual(summary["worker_state"], "crashed")
                self.assertIn("cannot continue", summary["worker_error"])
                with (
                    self.patched_storage(),
                    mock.patch.object(
                        plugin_runtime,
                        "enqueue_worker_operation",
                    ) as enqueue,
                    self.assertRaisesRegex(PluginRunError, "cannot continue"),
                ):
                    manager.add_agents(run_id, 1)
                enqueue.assert_not_called()
            finally:
                manager.close()

    def test_retry_is_queued_to_the_existing_persistent_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.results[1]["status"] = "failed"
                    run.agent_statuses[1] = "failed"
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "idle"
                    run.worker_operation = ""
                    run.worker_operation_id = ""
                    run.worker_pid = os.getpid()
                    manager._detached_workers = True
                    manager._save_state_locked()

                with mock.patch.object(
                    plugin_runtime,
                    "enqueue_worker_operation",
                    return_value=("retry-operation", root / "request.json"),
                ) as enqueue:
                    retried = manager.retry_agent(run_id, 1)

                self.assertEqual(retried["worker_pid"], os.getpid())
                self.assertEqual(retried["worker_operation"], "retry")
                self.assertEqual(enqueue.call_args.args[1:3], (run_id, "retry"))
            finally:
                manager.close()

    def test_retry_and_more_join_an_active_persistent_candidate_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=2)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.results[1]["status"] = "failed"
                    run.agent_statuses[1] = "failed"
                    run.status = "running"
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "busy"
                    run.worker_operation = "initial"
                    run.worker_operation_id = "initial-operation"
                    run.worker_pid = os.getpid()
                    manager._detached_workers = True
                    plugin_lifecycle.initialize_candidate_requests(
                        plugin_lifecycle.worker_candidate_requests_path(
                            manager.state_dir,
                            run_id,
                        )
                    )
                    plugin_lifecycle.write_json_atomic(
                        plugin_lifecycle.worker_synthesis_config_path(
                            manager.state_dir,
                            run_id,
                        ),
                        {
                            "agents": 2,
                            "model": None,
                            "effort": None,
                            "fast": None,
                            "locked": False,
                        },
                    )
                    manager._save_state_locked()

                summary = manager.get_run(run_id)
                failed = next(
                    agent for agent in summary["agents"] if agent["agent"] == 1
                )
                self.assertTrue(failed["can_retry"])
                self.assertTrue(summary["can_add_agents"])
                self.assertTrue(summary["can_update_synthesis"])

                retried = manager.retry_agent(run_id, 1)
                self.assertEqual(retried["status"], "running")
                self.assertEqual(retried["worker_operation"], "initial")
                self.assertEqual(retried["agents"][0]["status"], "queued")

                with self.patched_storage():
                    expanded = manager.add_agents(run_id, 1, confirm_large_run=True)
                self.assertEqual(expanded["worker_operation"], "initial")
                self.assertEqual(expanded["config"]["num_agents"], 3)

                control = plugin_lifecycle.read_json(
                    plugin_lifecycle.worker_candidate_requests_path(
                        manager.state_dir,
                        run_id,
                    )
                )
                self.assertEqual(
                    control["requests"],
                    [
                        {"idx": 1, "retry": True},
                        {"idx": 3, "retry": False},
                    ],
                )
            finally:
                manager.close()

    def test_late_more_marks_old_synthesis_stale_and_requests_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=2)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    synthesis = make_result(
                        Path(run.run_root),
                        3,
                        tokens=900,
                    )
                    synthesis.role = "synthesis"
                    run.results[3] = asdict(synthesis)
                    run.agent_statuses[3] = "success"
                    run.agent_roles[3] = "synthesis"
                    run.config["synthesis_agents"] = 1
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "idle"
                    run.worker_operation = ""
                    run.worker_pid = os.getpid()
                    manager._detached_workers = True
                    manager._save_state_locked()

                with (
                    self.patched_storage(),
                    mock.patch.object(
                        plugin_runtime,
                        "enqueue_worker_operation",
                        return_value=("more-operation", root / "request.json"),
                    ) as enqueue,
                ):
                    expanded = manager.add_agents(
                        run_id,
                        1,
                        confirm_large_run=True,
                    )

                request = enqueue.call_args.args[3]
                self.assertTrue(request["refresh_synthesis"])
                self.assertEqual(request["agent_role"], "candidate")
                self.assertEqual(request["indices"], [4])
                self.assertEqual(expanded["config"]["num_agents"], 3)
                old_synthesis = next(
                    agent for agent in expanded["agents"] if agent["agent"] == 3
                )
                self.assertTrue(old_synthesis["stale"])
                self.assertFalse(old_synthesis["recommended"])
                self.assertFalse(old_synthesis["can_accept"])
                self.assertFalse(old_synthesis["can_continue"])
            finally:
                manager.close()

    def test_synthesis_retry_keeps_its_role_in_the_persistent_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=2)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    synthesis = make_result(
                        Path(run.run_root),
                        3,
                        status="failed",
                    )
                    synthesis.role = "synthesis"
                    run.results[3] = asdict(synthesis)
                    run.agent_statuses[3] = "failed"
                    run.agent_roles[3] = "synthesis"
                    run.config["synthesis_agents"] = 1
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "idle"
                    run.worker_operation = ""
                    run.worker_pid = os.getpid()
                    manager._detached_workers = True
                    manager._save_state_locked()

                with mock.patch.object(
                    plugin_runtime,
                    "enqueue_worker_operation",
                    return_value=("retry-operation", root / "request.json"),
                ) as enqueue:
                    retried = manager.retry_agent(run_id, 3)

                request = enqueue.call_args.args[3]
                self.assertEqual(request["agent_role"], "synthesis")
                self.assertFalse(request["refresh_synthesis"])
                retried_agent = next(
                    agent for agent in retried["agents"] if agent["agent"] == 3
                )
                self.assertEqual(retried_agent["role"], "synthesis")
                self.assertEqual(retried_agent["status"], "queued")
            finally:
                manager.close()

    def test_failed_persistent_queue_restores_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state")
            try:
                completed = self.start_completed_run(root, manager, num_agents=1)
                run_id = completed["run_id"]
                with manager._lock:
                    run = manager._runs[run_id]
                    run.worker_protocol = WORKER_PROTOCOL_VERSION
                    run.worker_state = "idle"
                    run.worker_operation = ""
                    run.worker_pid = os.getpid()
                    manager._detached_workers = True
                    manager._save_state_locked()

                with (
                    self.patched_storage(),
                    mock.patch.object(
                        plugin_runtime,
                        "enqueue_worker_operation",
                        side_effect=OSError("disk full"),
                    ),
                    self.assertRaisesRegex(PluginRunError, "disk full"),
                ):
                    manager.add_agents(run_id, 1)

                restored = manager.get_run(run_id)
                self.assertEqual(restored["status"], "completed")
                self.assertEqual(restored["config"]["num_agents"], 1)
                self.assertEqual(len(restored["agents"]), 1)
                self.assertEqual(restored["worker_state"], "idle")
                self.assertIsNone(restored["worker_operation"])
            finally:
                manager.close()


class EventLogTests(unittest.TestCase):
    def test_lazily_indexes_complete_lines_and_discards_partial_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            run_id = "pcr-20260713T120000Z-1234abcd"
            events.event_path(run_id).write_bytes(
                b'{"type":"one"}\n{"type":"partial"'
            )
            page = events.read(run_id, cursor=0, limit=10)
            self.assertEqual([event["type"] for event in page["events"]], ["one"])
            self.assertEqual(page["total"], 1)
            event_id = events.append(run_id, {"type": "two"})
            self.assertEqual(event_id, 1)
            self.assertEqual(events.count(run_id), 2)

    def test_byte_bounded_pages_do_not_skip_or_duplicate_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            run_id = "pcr-20260713T120000Z-1234abcd"
            for index in range(5):
                events.append(
                    run_id,
                    {
                        "type": "agent_line",
                        "idx": 1,
                        "text": f"line-{index}-" + ("x" * 2_000),
                    },
                )

            cursor = 0
            received: list[int] = []
            while True:
                page = events.read(
                    run_id,
                    cursor=cursor,
                    limit=100,
                    max_bytes=4_096,
                )
                self.assertLessEqual(page["serialized_bytes"], 4_096)
                received.extend(event["event_id"] for event in page["events"])
                self.assertGreater(page["next_cursor"], cursor)
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break

            self.assertEqual(received, list(range(5)))

    def test_agent_filter_advances_cursor_across_other_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            run_id = "pcr-20260713T120000Z-1234abcd"
            for agent in (2, 1, 2, 1):
                events.append(
                    run_id,
                    {"type": "agent_line", "idx": agent, "text": str(agent)},
                )

            first = events.read(run_id, agent=1, cursor=0, limit=1)
            second = events.read(
                run_id,
                agent=1,
                cursor=first["next_cursor"],
                limit=1,
            )

            self.assertEqual([event["event_id"] for event in first["events"]], [1])
            self.assertEqual([event["event_id"] for event in second["events"]], [3])
            self.assertFalse(second["has_more"])

    def test_transcript_kind_filter_skips_operational_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            run_id = "pcr-20260713T120000Z-1234abcd"
            for item_type, text in (
                ("agent_reasoning", "thinking"),
                ("command_execution", "pwd"),
                ("agent_message", "answer"),
            ):
                events.append(
                    run_id,
                    {
                        "type": "agent_line",
                        "idx": 1,
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {"type": item_type, "text": text},
                            }
                        ),
                    },
                )

            page = events.read(
                run_id,
                agent=1,
                transcript_kinds={"output", "warning", "error"},
            )

            self.assertEqual([event["event_id"] for event in page["events"]], [2])
            self.assertEqual(page["next_cursor"], 3)
            self.assertFalse(page["has_more"])

    def test_oversized_first_event_is_explicit_and_cursor_progresses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            run_id = "pcr-20260713T120000Z-1234abcd"
            events.append(
                run_id,
                {"type": "agent_line", "idx": 1, "text": "x" * 8_000},
            )

            page = events.read(run_id, max_bytes=4_096)

            self.assertTrue(page["oversized_event"])
            self.assertEqual(page["next_cursor"], 1)
            self.assertFalse(page["has_more"])

    def test_large_agent_line_is_split_before_it_reaches_the_wire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            run_id = "pcr-20260713T120000Z-1234abcd"
            text = "output-" + ("中文内容" * 80_000)
            events.append(
                run_id,
                {
                    "type": "agent_line",
                    "idx": 1,
                    "text": json.dumps(
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": text},
                        },
                        ensure_ascii=False,
                    ),
                },
            )

            cursor = 0
            reconstructed: list[str] = []
            while True:
                page = events.read(
                    run_id,
                    cursor=cursor,
                    limit=100,
                    max_bytes=256 * 1024,
                )
                self.assertFalse(page["oversized_event"])
                self.assertLessEqual(page["serialized_bytes"], 256 * 1024)
                for event in page["events"]:
                    reconstructed.append(event["pcr_transcript_text"])
                self.assertGreater(page["next_cursor"], cursor)
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break

            self.assertEqual("".join(reconstructed), text)


if __name__ == "__main__":
    unittest.main()
