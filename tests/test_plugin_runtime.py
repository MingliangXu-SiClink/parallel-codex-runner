import json
import os
import shutil
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
    PluginRunManager,
)
from parallel_codex_runner_core.plugin.artifacts import RUN_MARKER_NAME
from parallel_codex_runner_core.plugin.events import EventLog


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
        for idx in range(1, args.num_agents + 1):
            progress_callback({"type": "agent_status", "idx": idx, "status": "copying"})
            result = make_result(run_root, idx, tokens=idx * 100)
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
    ) -> dict:
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "tracked.txt").write_text("original\n", encoding="utf-8")
        runs_dir = root / "runs"
        with (
            self.patched_storage(),
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
        return completed

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
                    with self.assertRaisesRegex(
                        PluginRunError, "retry cleanup"
                    ):
                        manager.accept_agent(completed["run_id"], 1)
                    failed = manager.get_run(completed["run_id"])
                    self.assertEqual(failed["status"], "cleanup_failed")
                    self.assertEqual(failed["finalized_agent"], 1)
                    self.assertTrue(failed["synced_back"])
                    finalized = manager.accept_agent(completed["run_id"], 1)

                self.assertEqual(finalized["status"], "finalized")
                self.assertTrue(finalized["workspaces_deleted"])
                self.assertEqual(attempts, 2)
                sync.assert_called_once()
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
                CodexHistoryEntry("user", "Fix the parser"),
                CodexHistoryEntry("thought", "Inspecting the grammar"),
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
                self.assertTrue(first["has_more"])
                self.assertEqual(
                    [entry["category"] for entry in first["entries"]],
                    ["user", "thought"],
                )
                self.assertFalse(second["has_more"])
                self.assertEqual(second["entries"][0]["text"], "The parser is fixed")
                list_sessions.assert_called_once()
                resume_error.assert_called_once()
                load_history.assert_called_once()
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
                    self.assertEqual(second.list_runs()["total"], 1)
                finally:
                    second.close()
            finally:
                first.close()

    def test_detached_worker_survives_manager_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "tracked.txt").write_text("original\n", encoding="utf-8")
            codex_home = root / "codex-home"
            codex_home.mkdir()
            fake_codex = root / "codex"
            fake_codex.write_text(
                """#!/bin/sh
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
  printf '%s\\n' 'Usage: codex exec [OPTIONS]' '  --json' '  --output-last-message FILE'
  exit 0
fi
output=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then output=$2; shift 2; else shift; fi
done
sleep 0.6
if [ -n "$output" ]; then printf '%s\\n' 'detached result' > "$output"; fi
printf '%s\\n' '{"type":"thread.started","thread_id":"detached-session"}'
printf '%s\\n' '{"type":"turn.completed","usage":{"reasoning_output_tokens":42}}'
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            state_dir = root / "state"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                manager = PluginRunManager(state_dir, detached_workers=True)
                response = manager.start_run(
                    prompt="slow shutdown",
                    workspace=str(workspace),
                    runs_dir=str(root / "runs"),
                    codex_bin=str(fake_codex),
                    num_agents=1,
                )
                run_id = response["run_id"]
                pid = response["run"]["worker_pid"]
                self.assertTrue(pid)
                manager.close(wait_seconds=0)

                recovered = PluginRunManager(state_dir, detached_workers=True)
                try:
                    completed = recovered.wait_for_run(run_id, timeout_seconds=10)
                    self.assertEqual(completed["status"], "completed")
                    self.assertFalse(completed["worker_active"])
                    self.assertEqual(completed["agents"][0]["status"], "success")
                finally:
                    recovered.close()


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


if __name__ == "__main__":
    unittest.main()
