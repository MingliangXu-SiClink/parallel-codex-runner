import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from concurrent.futures import ThreadPoolExecutor

from parallel_codex_runner_core.plugin.lifecycle import WORKER_PROTOCOL_VERSION
from parallel_codex_runner_core.plugin.events import EventLog
from parallel_codex_runner_core.plugin.state import JsonStateStore, ManagedRun
from parallel_codex_runner_core.plugin_runtime import PluginRunManager
from parallel_codex_runner_core.agent_questions import QuestionStore
from parallel_codex_runner_core.remote.notifications import RunCompletionMonitor
from parallel_codex_runner_core.remote.controller import RemoteController
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from parallel_codex_runner_core import plugin_runtime
from parallel_codex_runner_core.models import CodexHistoryEntry, ResumeSession
from parallel_codex_runner_core.visible_paths import WorkspacePathMapping


class InteractiveReadTests(unittest.TestCase):
    def test_operation_receipt_does_not_republish_an_unchanged_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="finalized", finalized_agent=1,
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="stopped")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            before = manager.state_path.stat()
            operation_id = run.run_id + "-receipt"
            response_path = plugin_runtime.worker_response_path(manager.state_dir, operation_id)
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response = {"ok": True, "operation": "finalize", "result": {"status": "finalized"}}
            response_path.write_text(json.dumps(response))
            with mock.patch.object(manager._state_store, "write_runs", wraps=manager._state_store.write_runs) as write:
                self.assertEqual(manager._wait_for_persistent_operation(run.run_id, operation_id, 1), response)
                write.assert_not_called()
            after = manager.state_path.stat()
            self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
            self.assertFalse(response_path.exists())

    def test_operation_receipt_still_persists_a_new_terminal_owner_state(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="finalized", finalized_agent=1,
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy",
                worker_pid=999999999, worker_operation="finalize", worker_operation_id="finished")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            operation_id = run.run_id + "-receipt"
            response_path = plugin_runtime.worker_response_path(manager.state_dir, operation_id)
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(json.dumps({"ok": True, "operation": "finalize"}))
            with mock.patch.object(manager, "_worker_alive_locked", return_value=False), \
                 mock.patch.object(manager._state_store, "write_runs", wraps=manager._state_store.write_runs) as write:
                self.assertTrue(manager._wait_for_persistent_operation(run.run_id, operation_id, 1)["ok"])
                self.assertEqual(write.call_count, 1)
            saved = manager._state_store.load_payload()["runs"][0]
            self.assertEqual(saved["worker_state"], "stopped")
            self.assertIsNone(saved["worker_pid"])
            self.assertEqual(saved["worker_operation"], "")
            self.assertEqual(saved["finalized_agent"], 1)

    def test_history_memory_cache_revalidates_same_size_same_mtime_replacement(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory) / "manager") as manager:
            root = Path(directory)
            rollout = root / "rollout.jsonl"

            def content(text):
                return json.dumps({"type": "event_msg", "payload": {
                    "type": "agent_message", "message": text,
                }}) + "\n"

            rollout.write_text(content("old answer"))
            original = rollout.stat()
            session = ResumeSession("session", "fixture", str(root), 1, rollout_path=str(rollout))
            with mock.patch.object(plugin_runtime, "get_codex_home", return_value=root), \
                 mock.patch.object(plugin_runtime, "list_resume_sessions", return_value=[session]), \
                 mock.patch.object(plugin_runtime, "subagent_resume_error", return_value=None):
                def read():
                    return manager._resume_history_page(root, "session", cursor=0, limit=10)["entries"][0]["text"]

                self.assertEqual(read(), "old answer")
                replacement = root / "replacement.jsonl"
                replacement.write_text(content("new answer"))
                os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
                os.replace(replacement, rollout)
                self.assertEqual(rollout.stat().st_size, original.st_size)
                self.assertEqual(rollout.stat().st_mtime_ns, original.st_mtime_ns)
                self.assertEqual(read(), "new answer")
                # Editing in place while restoring mtime must not bypass the cache either.
                rollout.write_text(content("end answer"))
                os.utime(rollout, ns=(original.st_atime_ns, original.st_mtime_ns))
                self.assertEqual(read(), "end answer")

    def test_registry_generations_do_not_evict_workspace_history(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory) / "manager") as manager:
            root = Path(directory)
            state = root / "state_5.sqlite"
            state.write_bytes(b"generation-0")
            sources = [root / f"workspace-{index}" for index in range(5)]
            sessions = {source: ResumeSession(str(index), "fixture", str(source), 1,
                        rollout_path=str(root / f"rollout-{index}")) for index, source in enumerate(sources)}
            # Keep the old eight-entry budget to expose cross-type eviction,
            # independent of the larger production working set.
            manager._resume_cache.capacity = 8
            manager._resume_session_cache.capacity = 8
            with mock.patch.object(plugin_runtime, "get_codex_home", return_value=root), \
                 mock.patch.object(plugin_runtime, "list_resume_sessions", side_effect=lambda source, **_: [sessions[source]]) as discover, \
                 mock.patch.object(plugin_runtime, "subagent_resume_error", return_value=None), \
                 mock.patch.object(plugin_runtime, "load_codex_session_history", return_value=[CodexHistoryEntry("user", "first"), CodexHistoryEntry("output", "answer")]) as load:
                for generation in range(3):
                    state.write_bytes(b"registry-" + b"x" * generation)
                    for source in sources:
                        for cursor in (0, 1):
                            page = manager._resume_history_page(source, sessions[source].session_id,
                                cursor=cursor, limit=1, conversation_items_only=True)
                            self.assertEqual(page["entries"][0]["text"], "first" if cursor == 0 else "answer")
                self.assertEqual(load.call_count, 5)
                self.assertEqual(discover.call_count, 15)

    def test_history_projection_is_shared_across_pages_and_concurrent_readers(self):
        history = plugin_runtime._HistorySnapshot([
            CodexHistoryEntry("user", "previous"),
            CodexHistoryEntry("output", "old answer"),
            CodexHistoryEntry("thought", "work"),
            CodexHistoryEntry("user", "current"),
        ])
        with mock.patch.object(plugin_runtime, "_history_payloads", wraps=plugin_runtime._history_payloads) as project:
            with ThreadPoolExecutor(max_workers=5) as pool:
                replies = list(pool.map(lambda _: history.project("current", True, ()), range(5)))
            project.assert_called_once()
            self.assertEqual(replies[0][1], 3)
            self.assertEqual([item["text"] for item in replies[0][0]], ["previous", "old answer"])
            self.assertTrue(all(reply[0] is replies[0][0] for reply in replies))
            self.assertEqual(len(history.project("", False, ())[0]), 4)
            self.assertEqual(project.call_count, 2)

    def test_history_projection_revalidates_created_and_deleted_link_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            new.mkdir()
            mapping = WorkspacePathMapping.create(old, new)
            raw = f"[result]({old}/result.txt)"
            history = plugin_runtime._HistorySnapshot([CodexHistoryEntry("output", raw)])
            self.assertEqual(history.project("", True, [mapping])[0][0]["text"], raw)
            (new / "result.txt").write_text("fixture")
            self.assertIn(str(new / "result.txt"), history.project("", True, [mapping])[0][0]["text"])
            (new / "result.txt").unlink()
            self.assertEqual(history.project("", True, [mapping])[0][0]["text"], raw)
            new.rmdir()
            self.assertEqual(history.project("", True, [mapping])[0][0]["text"], raw)
            new.mkdir()
            (new / "result.txt").write_text("fixture")
            self.assertIn(str(new / "result.txt"), history.project("", True, [mapping])[0][0]["text"])

    def test_event_lock_in_one_workspace_does_not_block_another(self):
        with tempfile.TemporaryDirectory() as directory:
            events = EventLog(Path(directory))
            events.append("a", {"type": "agent_line", "text": "a"})
            events.append("b", {"type": "agent_line", "text": "b"})
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            def held():
                with events._locked("a"):
                    entered.set()
                    release.wait(5)
            def read():
                events.read("b")
                finished.set()
            holding = threading.Thread(target=held)
            reading = threading.Thread(target=read)
            holding.start()
            self.assertTrue(entered.wait(2))
            reading.start()
            try:
                self.assertTrue(finished.wait(1), "Different runs shared an event mutex")
            finally:
                release.set()
                holding.join(5)
                reading.join(5)

    def test_validated_event_tail_is_reused_without_touching_files(self):
        with tempfile.TemporaryDirectory() as directory:
            events = EventLog(Path(directory))
            events.append("a", {"type": "agent_line", "text": "first"})
            before = events._index_stamp("a")
            with mock.patch.object(events, "_validate_index_locked", wraps=events._validate_index_locked) as validate:
                for _ in range(3):
                    self.assertEqual(len(events.read("a")["events"]), 1)
                validate.assert_not_called()
                self.assertEqual(events._index_stamp("a"), before)
                peer = EventLog(Path(directory))
                peer.append("a", {"type": "agent_line", "text": "second"})
                self.assertEqual(len(events.read("a")["events"]), 2)
                validate.assert_called_once()

    def make_run(self, index=1, **values):
        return ManagedRun(run_id=f"pcr-20261027T120000Z-{index:08x}",
                          prompt="fixture", workspace="/fixture", config={}, **values)

    def test_health_does_not_replay_history_or_run_lifecycle_operations(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            with mock.patch.object(manager, "_run_maintenance_locked") as maintenance:
                self.assertTrue(manager.health()["ok"])
            maintenance.assert_not_called()

    def test_observing_owner_progress_does_not_rewrite_the_shared_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy", agent_statuses={1: "queued"})
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            manager._events.append(run.run_id, {"type": "agent_started", "idx": 1})
            with mock.patch.object(manager, "_save_state_locked", wraps=manager._save_state_locked) as save:
                self.assertEqual(manager.get_run(run.run_id)["agents"][0]["status"], "running")
                manager.get_run_overviews([run.run_id])
                manager.completion_snapshot()
                self.assertEqual(manager.rewrite_agent_visible_texts(run.run_id, 1, ["visible"]), ["visible"])
                with mock.patch.object(manager, "_resolve_workspace", return_value=Path(directory)):
                    self.assertEqual(manager.run_history(run.run_id)["entries"], [])
                save.assert_not_called()
            # A later business transaction still commits its original state.
            with manager._lock:
                manager._require_run_locked(run.run_id)
            self.assertEqual(manager._state_store.load_payload()["runs"][0]["agent_statuses"]["1"], "running")

    def test_workspace_overview_does_not_replay_other_workspaces(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            runs = [self.make_run(index, status="running", worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy") for index in (1, 2)]
            manager._runs.update((run.run_id, run) for run in runs)
            manager._save_state_locked()
            with mock.patch.object(manager._events, "read", wraps=manager._events.read) as replay:
                response = manager.get_run_overviews([runs[0].run_id])
            self.assertEqual([item["run_id"] for item in response["runs"]], [runs[0].run_id])
            self.assertEqual({call.args[0] for call in replay.call_args_list}, {runs[0].run_id})

    def test_timing_progress_is_replayable_without_shared_checkpoint_writes(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy", agent_statuses={1: "running"})
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            before = manager.state_path.read_bytes()
            events = [
                {"type": "run_phase", "phase": "workspace_sync", "phase_timings": {"workspace_sync": 0.2}},
                {"type": "agent_phase", "idx": 1, "phase": "resuming", "phase_timings": {"initializing": 0.1}},
            ]
            with mock.patch.object(manager, "_save_state_locked", wraps=manager._save_state_locked) as save:
                for event in events:
                    manager._record_progress(run.run_id, event)
                manager.get_run(run.run_id)
                with manager._lock:
                    manager._require_run_locked(run.run_id)
                save.assert_not_called()
            self.assertEqual(manager.state_path.read_bytes(), before)
            # A cold reader rebuilds timing without turning it into a business
            # mutation or overwriting another workspace's shared checkpoint.
            with PluginRunManager(Path(directory)) as reader:
                with mock.patch.object(reader, "_save_state_locked", wraps=reader._save_state_locked) as save:
                    summary = reader.get_run(run.run_id)
                    with reader._lock:
                        reader._require_run_locked(run.run_id)
                    save.assert_not_called()
                self.assertEqual(summary["phase"], "workspace_sync")
                self.assertEqual(summary["phase_timings"]["workspace_sync"], 0.2)
                self.assertEqual(summary["agents"][0]["phase"], "resuming")
            with mock.patch.object(manager, "_save_state_locked", wraps=manager._save_state_locked) as save:
                manager._record_progress(run.run_id, {"type": "agent_started", "idx": 1})
                save.assert_called_once()
            self.assertEqual(manager._state_store.load_payload()["runs"][0]["event_count"], 3)

    def test_slow_target_event_read_does_not_block_another_workspaces_health(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", worker_pid=os.getpid(),
                               worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            entered, release, responsive = threading.Event(), threading.Event(), threading.Event()
            errors = []
            original = manager._events.read

            def slow_read(*args, **kwargs):
                entered.set()
                release.wait(5)
                return original(*args, **kwargs)

            def read_target():
                try:
                    manager.get_run(run.run_id)
                except Exception as error:
                    errors.append(error)

            def health():
                manager.health()
                responsive.set()

            with mock.patch.object(manager._events, "read", side_effect=slow_read):
                reading = threading.Thread(target=read_target)
                reading.start()
                self.assertTrue(entered.wait(2))
                checking = threading.Thread(target=health)
                checking.start()
                try:
                    self.assertTrue(responsive.wait(1), "A log read held the global manager lock")
                finally:
                    release.set()
                    reading.join(5)
                    checking.join(5)
            self.assertEqual(errors, [])

    def test_concurrent_agent_readers_do_not_replay_the_same_progress_page(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", agent_statuses={1: "running"}, event_count=1)
            manager._runs[run.run_id] = run
            manager._events.append(run.run_id, {"type": "agent_started", "idx": 1,
                                               "timestamp": "2026-10-28T00:00:00Z"})
            entered, release = threading.Event(), threading.Event()
            original = manager._events.read

            def held(*args, **kwargs):
                entered.set()
                release.wait(5)
                return original(*args, **kwargs)

            with mock.patch.object(manager._events, "read", side_effect=held) as read, \
                 ThreadPoolExecutor(max_workers=8) as pool:
                first = pool.submit(manager._refresh_read_progress, run.run_id)
                self.assertTrue(entered.wait(2))
                others = [pool.submit(manager._refresh_read_progress, run.run_id) for _ in range(7)]
                try:
                    # These are optional activity/timing projections, not a
                    # reason to delay another Agent's actual conversation.
                    for future in others:
                        future.result(timeout=1)
                    self.assertEqual(read.call_count, 1)
                finally:
                    release.set()
                    first.result(timeout=5)
            self.assertEqual(manager._progress_cache.get(run.run_id).cursor, 1)

    def test_failed_progress_read_releases_its_single_flight(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", agent_statuses={1: "running"}, event_count=1)
            manager._runs[run.run_id] = run
            manager._events.append(run.run_id, {"type": "agent_started", "idx": 1})
            with mock.patch.object(manager._events, "read", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    manager._refresh_read_progress(run.run_id)
            manager._refresh_read_progress(run.run_id)
            self.assertEqual(manager._progress_cache.get(run.run_id).cursor, 1)

    def test_completion_sweeps_do_not_starve_other_runs_behind_a_large_tail(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            backlog = self.make_run(1, status="running", worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy", agent_statuses={1: "running"})
            ready = self.make_run(2, status="completed", worker_state="stopped", agent_statuses={1: "success"})
            manager._runs.update({backlog.run_id: backlog, ready.run_id: ready})
            manager._save_state_locked()
            for _ in range(1001):
                manager._events.append(backlog.run_id, {"type": "agent_line", "idx": 1, "text": "work"})
            with mock.patch.object(manager, "_schedule_async_answers_locked") as dispatch:
                records = manager.completion_snapshot()
            self.assertLess(backlog.event_count, 1001)
            self.assertTrue(next(row for row in records if row["run_id"] == backlog.run_id)["working"])
            dispatch.assert_called_once_with(ready)

    def test_question_import_does_not_hold_the_workspace_manager_lock(self):
        for action in ("run", "agent", "list"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
                run = self.make_run(status="running", worker_pid=os.getpid(),
                    worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy", agent_statuses={1: "running"})
                manager._runs[run.run_id] = run
                manager._save_state_locked()
                store = mock.Mock(spec=QuestionStore)
                store.directory = Path(directory)
                store.pending_count.return_value = 1
                store.blocking_count.return_value = 0
                store.snapshot.return_value = []
                entered, release, responsive = threading.Event(), threading.Event(), threading.Event()
                errors, responses = [], []

                def read_count():
                    entered.set()
                    release.wait(5)
                    return 1

                def read_target():
                    try:
                        if action == "run":
                            responses.append(manager.get_run(run.run_id))
                        elif action == "agent":
                            responses.append(manager.get_agent(run.run_id, 1, include_events=False))
                        else:
                            responses.append(manager.list_runs())
                    except Exception as error:
                        errors.append(error)

                def health():
                    manager.health()
                    responsive.set()

                store.pending_count.side_effect = read_count
                with mock.patch.object(manager, "_questions_if_present", return_value=store):
                    reading = threading.Thread(target=read_target)
                    reading.start()
                    self.assertTrue(entered.wait(2))
                    checking = threading.Thread(target=health)
                    checking.start()
                    try:
                        self.assertTrue(responsive.wait(1), "Question I/O blocked every workspace")
                        # A late read cannot restore pending questions after a user stop.
                        with manager._lock:
                            manager._runs[run.run_id].status = "stopping"
                    finally:
                        release.set()
                        reading.join(5)
                        checking.join(5)
                self.assertEqual(errors, [])
                result = responses[0]
                summary = result if action == "agent" else (result["agents"][0] if action == "run" else result["runs"][0]["agents"][0])
                self.assertEqual(summary["pending_question_count"], 0)

    def test_read_reconciliation_preserves_a_concurrent_owner_stop(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running", worker_pid=os.getpid(),
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="busy", agent_statuses={1: "running"})
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            manager._events.append(run.run_id, {"type": "agent_started", "idx": 1})
            original = manager._events.read
            replaced = False

            def late_read(*args, **kwargs):
                nonlocal replaced
                page = original(*args, **kwargs)
                if not replaced:
                    replaced = True
                    with manager._lock:
                        stopped = ManagedRun.from_dict(run.to_dict())
                        stopped.status = "cancelled"
                        stopped.worker_pid = None
                        stopped.worker_state = "stopped"
                        stopped.agent_statuses[1] = "killed"
                        stopped.event_count = page["total"]
                        stopped.updated_at = "2099-01-01T00:00:00+00:00"
                        manager._runs[run.run_id] = stopped
                        manager._save_state_locked()
                return page

            with mock.patch.object(manager._events, "read", side_effect=late_read):
                self.assertTrue(manager._reconcile_read_events(run.run_id))
            self.assertEqual(manager._runs[run.run_id].agent_statuses[1], "killed")
            self.assertEqual(manager._runs[run.run_id].status, "cancelled")

    def test_event_refresh_has_a_fixed_tail_when_the_worker_keeps_appending(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="running")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            manager._events.append(run.run_id, {"type": "agent_line", "idx": 1, "text": "first"})
            original = manager._events.read

            def appending(*args, **kwargs):
                page = original(*args, **kwargs)
                manager._events.append(run.run_id, {"type": "agent_line", "idx": 1, "text": "next"})
                return page

            with mock.patch.object(manager._events, "read", side_effect=appending) as reads:
                self.assertTrue(manager._reconcile_read_events(run.run_id))
            self.assertEqual(run.event_count, 1)
            self.assertEqual(manager._events.count(run.run_id), 2)
            self.assertEqual(reads.call_count, 1)

    def test_stopped_finalized_owner_does_not_reopen_archived_events(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="finalized", worker_protocol=WORKER_PROTOCOL_VERSION,
                                worker_state="stopped", finalized_agent=1)
            with mock.patch.object(manager, "_refresh_events_locked", return_value=False) as replay:
                self.assertFalse(manager._detect_worker_failure_locked(run))
            replay.assert_not_called()

    def test_atomic_state_reader_does_not_wait_for_a_peer_writer(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="completed")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            peer = JsonStateStore(Path(directory))
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            errors = []

            def writer():
                with peer.locked():
                    entered.set()
                    release.wait(5)

            def reader():
                try:
                    with manager._lock:
                        manager._sync_state_from_disk_locked(force=True)
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            writing, reading = threading.Thread(target=writer), threading.Thread(target=reader)
            writing.start()
            self.assertTrue(entered.wait(2))
            reading.start()
            try:
                self.assertTrue(finished.wait(1), "An immutable state read waited for the writer lock")
            finally:
                release.set()
                writing.join(5)
                reading.join(5)
            self.assertEqual(errors, [])

    def test_post_write_peer_update_is_not_mistaken_for_our_own_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="completed")
            manager._runs[run.run_id] = run
            peer = JsonStateStore(Path(directory))

            def peer_update(_path, _mode):
                changed = ManagedRun.from_dict(run.to_dict())
                changed.prompt = "new message from peer"
                changed.updated_at = "2099-01-01T00:00:00+00:00"
                with peer.locked():
                    peer.write_runs({changed.run_id: changed})

            with mock.patch.object(manager, "_harden_path", side_effect=peer_update):
                manager._save_state_locked()
            manager._sync_state_from_disk_locked()
            self.assertEqual(manager._runs[run.run_id].prompt, "new message from peer")

    def test_state_writer_reuses_exact_published_or_loaded_generation(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="completed")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            original_stamp = manager._state_file_stamp
            run.prompt = "local continuation"
            with mock.patch.object(manager._state_store, "load_payload", side_effect=AssertionError("re-parsed known state")), \
                 mock.patch.object(manager._state_store, "write_runs", wraps=manager._state_store.write_runs) as writes:
                manager._save_state_locked()
            writes.assert_called_once()
            self.assertNotEqual(manager._state_file_stamp, original_stamp)
            self.assertEqual(manager._state_store.load_payload()["runs"][0]["prompt"], "local continuation")

            with PluginRunManager(Path(directory), run_maintenance=False) as reader:
                self.assertEqual(reader._state_file_stamp,
                                 reader._state_store.stamp(reader.state_path.stat()))
                with mock.patch.object(reader._state_store, "load_snapshot", side_effect=AssertionError("re-parsed startup snapshot")):
                    reader._sync_state_from_disk_locked()
                reader._runs[run.run_id].prompt = "next continuation"
                with mock.patch.object(reader._state_store, "load_payload", side_effect=AssertionError("re-parsed loaded state")):
                    reader._save_state_locked()
            self.assertEqual(manager._state_store.load_payload()["runs"][0]["prompt"], "next continuation")

    def test_worker_result_operation_does_not_run_unrelated_maintenance(self):
        from parallel_codex_runner_core.plugin.worker import _manager_operation

        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(status="finalized", finalized_agent=1)
            other = self.make_run(index=2, status="running", worker_pid=12345)
            store = JsonStateStore(Path(directory))
            store.write_runs({run.run_id: run, other.run_id: other})
            with mock.patch.object(PluginRunManager, "_run_maintenance_locked") as maintenance, \
                 mock.patch.object(PluginRunManager, "_recover_finalization_locked") as recover, \
                 mock.patch.object(PluginRunManager, "_detect_worker_failure_locked", return_value=False) as detect, \
                 mock.patch.object(PluginRunManager, "_refresh_events_locked", return_value=False) as replay:
                result = _manager_operation(Path(directory), "finalize", {"agent": 1}, run)
            maintenance.assert_not_called()
            for call in [*recover.call_args_list, *detect.call_args_list, *replay.call_args_list]:
                self.assertEqual(call.args[0].run_id, run.run_id)
            self.assertEqual(result["finalized_agent"], 1)
            saved = {item['run_id']: item for item in store.load_payload()['runs']}
            self.assertEqual(saved[other.run_id]['status'], 'running')
            self.assertEqual(saved[other.run_id]['worker_pid'], 12345)

    def test_startup_snapshot_does_not_claim_a_peers_later_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(Path(directory))
            run = self.make_run(status="completed")
            store.write_runs({run.run_id: run})
            original = JsonStateStore.load_snapshot
            replaced = False

            def replace_after_read(instance):
                nonlocal replaced
                snapshot = original(instance)
                if not replaced:
                    replaced = True
                    changed = ManagedRun.from_dict(run.to_dict())
                    changed.prompt = "published after startup read"
                    changed.updated_at = "2099-01-01T00:00:00+00:00"
                    store.write_runs({run.run_id: changed})
                return snapshot

            with mock.patch.object(JsonStateStore, "load_snapshot", replace_after_read):
                with PluginRunManager(Path(directory), run_maintenance=False) as manager:
                    self.assertEqual(manager._runs[run.run_id].prompt, "fixture")
                    self.assertNotEqual(manager._state_file_stamp, store.stamp(store.path.stat()))
                    manager._save_state_locked()
                    self.assertEqual(manager._runs[run.run_id].prompt, "published after startup read")
            self.assertEqual(store.load_payload()["runs"][0]["prompt"], "published after startup read")

    def test_state_writer_merges_peer_publication_after_lock_wait(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="completed")
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            peer = JsonStateStore(Path(directory))
            changed = self.make_run(status="completed", updated_at="2099-01-01T00:00:00+00:00")
            changed.prompt = "peer result"
            added = self.make_run(index=2, status="completed")
            added.prompt = "another workspace"
            entered = threading.Event()
            original_lock = manager._state_store.locked
            errors = []

            def announced_lock():
                entered.set()
                return original_lock()

            def writer():
                try:
                    with manager._lock:
                        manager._save_state_locked()
                except Exception as error:
                    errors.append(error)

            with mock.patch.object(manager._state_store, "locked", side_effect=announced_lock), \
                 mock.patch.object(manager._state_store, "load_payload", wraps=manager._state_store.load_payload) as reads:
                with peer.locked():
                    thread = threading.Thread(target=writer)
                    thread.start()
                    self.assertTrue(entered.wait(2))
                    peer.write_runs({changed.run_id: changed, added.run_id: added})
                thread.join(5)
                self.assertFalse(thread.is_alive())
                reads.assert_called_once()
            self.assertEqual(errors, [])
            saved = {item["run_id"]: item for item in peer.load_payload()["runs"]}
            self.assertEqual(saved[run.run_id]["prompt"], "peer result")
            self.assertEqual(saved[added.run_id]["prompt"], "another workspace")
            with mock.patch.object(manager._state_store, "load_payload", side_effect=AssertionError("re-parsed merged baseline")):
                manager._save_state_locked()
            self.assertEqual({item["run_id"] for item in peer.load_payload()["runs"]}, {run.run_id, added.run_id})

    def test_state_writer_keeps_explicit_deletion_on_fast_and_merge_paths(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            kept = self.make_run(status="completed")
            removed = self.make_run(index=2, status="completed")
            for peer_update in (False, True):
                with self.subTest(peer_update=peer_update):
                    manager._runs[kept.run_id] = kept
                    manager._runs[removed.run_id] = removed
                    manager._save_state_locked()
                    if peer_update:
                        peer = JsonStateStore(Path(directory))
                        with peer.locked():
                            peer.write_runs(manager._runs)
                    manager._save_state_locked(deleted_run_ids={removed.run_id})
                    self.assertEqual(list(manager._runs), [kept.run_id])
                    self.assertEqual([item["run_id"] for item in manager._state_store.load_payload()["runs"]], [kept.run_id])

    def test_state_writer_detects_replacement_with_same_mtime_and_size(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="completed", turn_id="fixture-turn",
                                updated_at="2098-01-01T00:00:00+00:00")
            run.prompt = "before"
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            original_stat = manager.state_path.stat()
            changed = ManagedRun.from_dict(run.to_dict())
            changed.prompt = "after!"
            changed.updated_at = "2099-01-01T00:00:00+00:00"
            peer = JsonStateStore(Path(directory))
            with peer.locked():
                peer.write_runs({changed.run_id: changed})
                os.utime(peer.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            self.assertEqual(peer.path.stat().st_size, original_stat.st_size)
            self.assertNotEqual(peer.path.stat().st_ino, original_stat.st_ino)
            manager._save_state_locked()
            self.assertEqual(peer.load_payload()["runs"][0]["prompt"], "after!")

    def test_replayed_activity_does_not_outvote_peer_business_checkpoint(self):
        for kind in sorted(plugin_runtime.REPLAY_ONLY_PROGRESS_EVENTS):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory, \
                 PluginRunManager(Path(directory)) as manager:
                run = self.make_run(status="running", updated_at="2097-01-01T00:00:00+00:00")
                manager._runs[run.run_id] = run
                manager._save_state_locked()
                completed = ManagedRun.from_dict(run.to_dict())
                completed.status = "completed"
                completed.updated_at = "2098-01-01T00:00:00+00:00"
                completed.agent_statuses = {1: "success"}
                completed.results = {1: {"status": "success", "final_message": "retained answer"}}
                peer = JsonStateStore(Path(directory))
                with peer.locked():
                    peer.write_runs({completed.run_id: completed})
                manager._apply_progress_locked(run, {"type": kind, "idx": 1,
                    "timestamp": "2099-01-01T00:00:00+00:00", "text": "late progress",
                    "reasoning_tokens": 99, "phase": "completed"})
                other = self.make_run(index=2, status="preparing")
                manager._runs[other.run_id] = other
                manager._save_state_locked()
                saved = {item["run_id"]: item for item in peer.load_payload()["runs"]}
                self.assertEqual(saved[run.run_id]["status"], "completed")
                self.assertEqual(saved[run.run_id]["results"]["1"]["final_message"], "retained answer")
                with mock.patch.object(manager._state_store, "load_payload", side_effect=AssertionError("re-parsed merged baseline")):
                    manager._save_state_locked()
                self.assertEqual(len(peer.load_payload()["runs"]), 2)

    def test_failed_state_publication_does_not_advance_known_generation(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced), tempfile.TemporaryDirectory() as directory, \
                 PluginRunManager(Path(directory)) as manager:
                run = self.make_run(status="completed")
                manager._runs[run.run_id] = run
                manager._save_state_locked()
                original_stamp = manager._state_file_stamp
                write = manager._state_store.write_runs

                def failed_write(runs):
                    if replaced:
                        write(runs)
                    raise OSError("injected publication failure")

                run.prompt = "retained local change"
                with mock.patch.object(manager._state_store, "write_runs", side_effect=failed_write):
                    with self.assertRaises(OSError):
                        manager._save_state_locked()
                self.assertEqual(manager._state_file_stamp, original_stamp)
                with mock.patch.object(manager._state_store, "load_payload", wraps=manager._state_store.load_payload) as reads:
                    manager._save_state_locked()
                self.assertEqual(reads.call_count, int(replaced))
                self.assertEqual(manager._state_store.load_payload()["runs"][0]["prompt"], "retained local change")

    def test_state_encoding_does_not_deepcopy_archived_result_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(Path(directory))
            run = self.make_run(status="finalized", results={1: {"reasoning_token_counts": {"8": 2}}})
            expected = json.loads(json.dumps(run.to_dict()))
            with mock.patch.object(ManagedRun, "to_dict", side_effect=AssertionError("deep copy on writer path")):
                store.write_runs({run.run_id: run})
            self.assertEqual(store.load_payload()["runs"], [expected])

    def test_existing_completion_monitor_drives_lifecycle_without_ui_reads(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            run = self.make_run(status="completed", worker_state="stopped",
                                agent_statuses={1: "success"})
            manager._runs[run.run_id] = run
            manager._save_state_locked()
            monitor = RunCompletionMonitor(manager, Path(directory) / "notifications.json",
                                           notifier=lambda *_: True)
            with mock.patch.object(manager, "health", side_effect=AssertionError("UI health dependency")), \
                 mock.patch.object(manager, "_schedule_async_answers_locked") as deliver:
                monitor.poll_once()
            deliver.assert_called_once_with(run)

    def test_authenticated_ping_needs_no_run_state_or_history_lock(self):
        with tempfile.TemporaryDirectory() as directory, PluginRunManager(Path(directory)) as manager:
            controller = RemoteController(state_dir=Path(directory) / "remote", manager=manager)
            try:
                with mock.patch.object(manager, "health", side_effect=AssertionError("aggregate on ping")), \
                     mock.patch.object(manager, "_sync_state_from_disk_locked", side_effect=AssertionError("state on ping")):
                    self.assertEqual(controller.dispatch(RemoteRequest(action="system.ping", body={})), {"status": "ok"})
            finally:
                controller.close()
