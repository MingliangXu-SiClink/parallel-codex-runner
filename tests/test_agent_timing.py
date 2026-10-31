import tempfile
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core.plugin.events import EventLog
from parallel_codex_runner_core.plugin.progress import RunProgressProjection
from parallel_codex_runner_core.plugin_runtime import ManagedRun, PluginRunManager


class AgentTimingTests(unittest.TestCase):
    def test_cold_summary_replay_is_bounded_resumable_and_not_reported_as_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp), detached_workers=False)
            run = ManagedRun(run_id="pcr-20260918T100000Z-12345678", prompt="QA",
                             workspace=tmp, config={}, status="running",
                             agent_statuses={1: "running", 2: "queued"})
            for _ in range(2_100):
                manager._events.append(run.run_id, {"type": "agent_status", "idx": 2, "status": "queued"})
            manager._events.append(run.run_id, {"type": "agent_started", "idx": 1,
                                                "timestamp": "2026-09-18T10:00:00Z"})
            run.event_count = manager._events.count(run.run_id)
            with mock.patch.object(manager._events, "read", wraps=manager._events.read) as read:
                first = manager._run_summary_locked(run)
                self.assertEqual(read.call_count, 1)
                self.assertIsNone(first["agents"][0]["started_at"])
                self.assertIsNone(first["agents"][0]["activity"])
                cursor = manager._progress_cache.get(run.run_id).cursor
                self.assertGreater(cursor, 0)
                self.assertLess(cursor, run.event_count)
                for _ in range(5):
                    read.reset_mock()
                    final = manager._run_summary_locked(run)
                    self.assertLessEqual(read.call_count, 1)
                    if final["agents"][0]["started_at"] is not None:
                        break
                self.assertEqual(final["agents"][0]["started_at"], "2026-09-18T10:00:00Z")
                self.assertEqual(manager._progress_cache.get(run.run_id).cursor, run.event_count)

    def test_synthesis_refresh_clears_the_previous_attempt_before_copying(self):
        projection = RunProgressProjection()
        for idx in (1, 2):
            projection.apply({"type": "agent_started", "idx": idx, "timestamp": "old-start"})
            projection.apply({"type": "agent_tokens", "idx": idx, "reasoning_token_counts": {"10": 2}})
            projection.apply({"type": "agent_finished", "idx": idx, "timestamp": "old-end"})
        projection.apply({"type": "synthesis_started", "indices": [2], "refresh": True})
        self.assertEqual(projection.started, {1: "old-start"})
        self.assertEqual(projection.finished, {1: "old-end"})
        self.assertEqual(projection.token_counts, {1: {"10": 2}})
        projection.apply({"type": "agent_started", "idx": 2, "timestamp": "new-start"})
        self.assertEqual(projection.started[2], "new-start")
        self.assertNotIn(2, projection.finished)

    def test_live_usage_snapshots_survive_reopening_and_do_not_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp), detached_workers=False)
            run = ManagedRun(run_id="pcr-20260919T100000Z-12345678", prompt="QA",
                             workspace=tmp, config={}, status="running",
                             agent_statuses={1: "running", 2: "running"})
            def append(payload):
                manager._events.append(run.run_id, payload)
                run.event_count += 1

            append({"type": "agent_started", "idx": 1, "timestamp": "start"})
            append({"type": "agent_tokens", "idx": 1, "reasoning_tokens": 70,
                    "reasoning_token_counts": {"20": 2, "30": 1}})
            first = manager._agent_summary_locked(run, 1)
            self.assertEqual(first["reasoning_token_counts"], {"20": 2, "30": 1})
            self.assertEqual(manager._agent_summary_locked(run, 2)["reasoning_token_counts"], {})
            for _ in range(2):
                append({"type": "agent_tokens", "idx": 1,
                        "reasoning_token_counts": {"20": 3, "30": 1}})
            self.assertEqual(manager._agent_summary_locked(run, 1)["reasoning_token_counts"],
                             {"20": 3, "30": 1})
            manager._progress_cache.clear()
            self.assertEqual(manager._agent_summary_locked(run, 1)["reasoning_token_counts"],
                             {"20": 3, "30": 1})
            append({"type": "batch_created", "indices": [1], "retry_indices": [1]})
            self.assertEqual(manager._agent_summary_locked(run, 1)["reasoning_token_counts"], {})
            append({"type": "agent_started", "idx": 1, "timestamp": "retry"})
            append({"type": "agent_tokens", "idx": 1,
                    "reasoning_token_counts": {"10": 1, "bad": 3, "-1": 4, "8": True}})
            self.assertEqual(manager._agent_summary_locked(run, 1)["reasoning_token_counts"], {"10": 1})
            run.results[1] = {"status": "success", "seconds": 10,
                              "reasoning_token_counts": {"10": 2}}
            self.assertEqual(manager._agent_summary_locked(run, 1)["reasoning_token_counts"], {"10": 2})

    def test_persisted_events_recover_independent_agent_starts_without_recounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = EventLog(Path(tmp))
            events.append("run", {"type": "agent_started", "idx": 1,
                                  "timestamp": "2026-09-18T10:00:00Z"})
            events.append("run", {"type": "agent_started", "idx": 2,
                                  "timestamp": "2026-09-18T10:02:00Z"})
            projection = RunProgressProjection()
            projection.refresh(events, "run", 2)
            self.assertEqual(projection.started[1], "2026-09-18T10:00:00Z")
            self.assertEqual(projection.started[2], "2026-09-18T10:02:00Z")
            with mock.patch.object(events, "read", wraps=events.read) as read:
                projection.refresh(events, "run", 2)
                read.assert_not_called()
            events.append("run", {"type": "agent_finished", "idx": 1,
                                  "timestamp": "2026-09-18T10:03:00Z"})
            with mock.patch.object(events, "read", wraps=events.read) as read:
                projection.refresh(events, "run", 3)
                self.assertEqual(read.call_args.kwargs["cursor"], 2)
            recovered = RunProgressProjection()
            recovered.refresh(EventLog(Path(tmp)), "run", 3)
            self.assertEqual(recovered, projection)
            self.assertNotIn(2, projection.finished)

    def test_retry_clears_the_previous_attempt_and_queue_does_not_start_the_clock(self):
        projection = RunProgressProjection()
        projection.apply({"type": "agent_started", "idx": 1, "timestamp": "start"})
        projection.apply({"type": "agent_finished", "idx": 1, "timestamp": "end"})
        projection.apply({"type": "batch_created", "indices": [1, 2], "retry_indices": [1]})
        projection.apply({"type": "agent_status", "idx": 1, "status": "copying", "timestamp": "copy"})
        self.assertEqual(projection.started, {})
        self.assertEqual(projection.finished, {})
        projection.apply({"type": "agent_started", "idx": 1, "timestamp": "retry"})
        projection.apply({"type": "agent_status", "idx": 1, "status": "waiting_for_models"})
        self.assertEqual(projection.started, {1: "retry"})
        projection.apply({"type": "run_failed", "timestamp": "failed"})
        self.assertEqual(projection.finished, {1: "failed"})

    def test_summary_exposes_real_start_from_an_older_worker_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp), detached_workers=False)
            run = ManagedRun(run_id="pcr-20260918T100000Z-12345678", prompt="QA",
                             workspace=tmp, config={}, status="running",
                             agent_statuses={1: "running", 2: "queued"}, event_count=2)
            manager._events.append(run.run_id, {"type": "agent_started", "idx": 1,
                                                "timestamp": "2026-09-18T10:00:00Z"})
            manager._events.append(run.run_id, {"type": "agent_status", "idx": 2,
                                                "status": "queued"})
            summary = manager._agent_summary_locked(run, 1)
            self.assertIsNone(summary["seconds"])
            self.assertEqual(summary["started_at"], "2026-09-18T10:00:00Z")
            self.assertIsNone(summary["finished_at"])
            self.assertIsNone(manager._agent_summary_locked(run, 2)["started_at"])
            manager._progress_cache.clear()
            self.assertEqual(manager._agent_summary_locked(run, 1)["started_at"], summary["started_at"])

    def test_complete_results_need_no_log_scan_and_do_not_invent_a_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp), detached_workers=False)
            run = ManagedRun(run_id="pcr-20260918T100000Z-12345678", prompt="QA",
                             workspace=tmp, config={}, status="completed",
                             results={1: {"status": "success", "seconds": 181.5}})
            with mock.patch.object(manager._events, "read") as read:
                summary = manager._agent_summary_locked(run, 1)
                read.assert_not_called()
            self.assertEqual(summary["seconds"], 181.5)
            self.assertIsNone(summary["started_at"])


if __name__ == "__main__":
    unittest.main()
