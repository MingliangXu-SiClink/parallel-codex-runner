import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.plugin.lifecycle import WORKER_PROTOCOL_VERSION
from parallel_codex_runner_core.plugin_runtime import PluginRunError, PluginRunManager, PluginRunPendingError
from parallel_codex_runner_core.remote.controller import RemoteController, RemoteControllerError
from parallel_codex_runner_core.remote.ipc import PcrdServer
from parallel_codex_runner_core.remote.journal import CommandJournal, CommandJournalError
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from parallel_codex_runner_core.scoped_history import HistorySchemaCompatibilityError
from .test_remote import FakeRunManager


class DurablePromptHandoffTests(unittest.TestCase):
    def test_history_adapter_failure_is_retained_by_the_daemon_across_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root / "state", manager=manager)
            workspace_id = controller.registry.register(str(root))["workspace"]["workspace_id"]
            request = RemoteRequest(action="turns.start", client_id="mac-app", command_id="history-original",
                body={"workspace_id": workspace_id, "prompt": "Original next question", "num_agents": 1})
            available = threading.Event()
            applied = []

            def start(current):
                if not available.is_set():
                    raise HistorySchemaCompatibilityError("Unsupported Codex history schema or migration")
                applied.append(CommandJournal.identity(current))
                return {"status": "started", "run_id": "pcr-original-once"}

            controller._actions[request.action] = start
            server = PcrdServer(root / "s", controller)
            try:
                response = server.execute(request.to_mapping())
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "pcr_operation_pending")
                self.assertEqual(server.prompt_receipt(request)["status"], "retryable")
                self.assertEqual(server.journal.pending_requests(), [request])
                self.assertFalse(applied)
            finally:
                server.server_close()
                controller.close()

            resumed = RemoteController(state_dir=root / "state", manager=manager)
            resumed._actions[request.action] = start
            server = PcrdServer(root / "s", resumed)
            server.prompt_delivery.poll_seconds = 0.01
            server.prompt_delivery.retry_seconds = 0.01
            server.prompt_delivery.start()
            try:
                self.assertTrue(server.prompt_receipt(request)["recovery_owned_by_daemon"])
                available.set()
                self.wait_for(lambda: server.prompt_receipt(request)["status"] == "completed")
                self.assertTrue(server.execute(request.to_mapping())["ok"])
                self.assertEqual(applied, [CommandJournal.identity(request)])
            finally:
                server.server_close()
                resumed.close()

    def test_legacy_history_failure_needs_committed_no_child_proof_and_the_original_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError(
                    "invalid_argument", "Unsupported Codex history schema; no global database was copied: future_table")))
            path = journal.directory / f"{journal._key(request)}.json"
            before = path.read_bytes()
            self.assertEqual(journal.lookup(request), {"status": "in_doubt"})
            self.assertEqual(journal.lookup(request, can_retry=lambda _: False), {"status": "in_doubt"})
            self.assertEqual(journal.lookup(request, can_retry=lambda _: True), {"status": "retryable"})
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(journal.pending_requests(), "A read cannot revive an abandoned original message")
            changed = RemoteRequest(action=request.action, client_id=request.client_id,
                command_id=request.command_id, body={**request.body, "prompt": "Edited question"})
            with self.assertRaises(CommandJournalError) as conflict:
                journal.lookup(changed, can_retry=lambda _: True)
            self.assertEqual(conflict.exception.code, "command_conflict")
            # A successful lookup is not a replay permit: the proof is checked
            # again under the original command lock before applying anything.
            with self.assertRaises(CommandJournalError):
                journal.execute(request, lambda: self.fail("Proof changed"), can_retry=lambda _: False)
            accepted, replayed = journal.execute(request, lambda: self.fail("Unguarded recovery"),
                can_retry=lambda _: True, retry_unstarted=lambda: {"run_id": "pcr-once"})
            self.assertFalse(replayed)
            self.assertEqual(accepted["run_id"], "pcr-once")
            self.assertEqual(json.loads(path.read_text())["history_recovery"]["error"]["code"], "invalid_argument")
            journal.execute(request, lambda: self.fail("Duplicate submit"), can_retry=lambda _: True)

    def test_legacy_history_recovery_keeps_its_guard_after_a_busy_retry_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = CommandJournal(root)
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError(
                    "invalid_argument", "Unsupported Codex history schema; no global database was copied: future_table")))
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: self.fail("Unguarded"), can_retry=lambda _: True,
                    retry_unstarted=lambda: (_ for _ in ()).throw(RemoteControllerError(
                        "pcr_operation_pending", "Another handoff won the lock", retryable=True)))
            resumed = CommandJournal(root)
            self.assertEqual(resumed.lookup(request, can_retry=lambda _: False), {"status": "in_doubt"})
            with self.assertRaises(CommandJournalError):
                resumed.execute(request, lambda: self.fail("Proof was lost"), can_retry=lambda _: False,
                    retry_unstarted=lambda: self.fail("Must not recover after another child appeared"))
            result, _ = resumed.execute(request, lambda: self.fail("Guard was dropped"), can_retry=lambda _: True,
                retry_unstarted=lambda: {"run_id": "pcr-once"})
            self.assertEqual(result["run_id"], "pcr-once")

    def test_legacy_history_recovery_prefers_an_existing_committed_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError(
                    "invalid_argument", "Unsupported Codex history schema; no global database was copied: future_table")))
            result, replayed = journal.execute(request, lambda: self.fail("Already accepted"),
                recover=lambda _: {"run_id": "pcr-committed"}, can_retry=lambda _: False)
            self.assertEqual(result["run_id"], "pcr-committed")
            self.assertTrue(replayed)

    def test_unrelated_invalid_arguments_are_never_retried_as_history_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError(
                    "invalid_argument", "Unsupported continuation settings: unknown")))
            self.assertEqual(journal.lookup(request, can_retry=lambda _: True), {"status": "in_doubt"})
            self.assertFalse(journal.pending_requests())

    def test_history_retry_proof_uses_committed_finalization_and_rejects_any_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = PluginRunManager(Path(temporary) / "state")
            run = ManagedRun("previous-turn", "old question", temporary, {"sync_back": True},
                status="finalized", results={7: {"status": "success"}}, finalized_agent=7,
                synced_back=True, session_promotion={"session_id": "original-session"})
            try:
                with manager._lock:
                    manager._runs[run.run_id] = run
                    manager._save_state_locked()
                self.assertTrue(manager.can_retry_unstarted_continuation(run.run_id, 7))
                self.assertFalse(manager.can_retry_unstarted_continuation(run.run_id, 8))
                child = ManagedRun("other-child", "another question", temporary, {},
                    continued_from_run_id=run.run_id)
                with manager._lock:
                    manager._runs[child.run_id] = child
                    manager._save_state_locked()
                self.assertFalse(manager.can_retry_unstarted_continuation(run.run_id, 7))
            finally:
                manager.close()

    def test_continuation_creation_and_legacy_retry_share_a_cross_manager_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            first, second = PluginRunManager(state), PluginRunManager(state)
            source = ManagedRun("previous-turn", "old", temporary, {"sync_back": True},
                status="finalized", finalized_agent=7, synced_back=True,
                session_promotion={"session_id": "original-session"})
            with first._lock:
                first._runs[source.run_id] = source
                first._save_state_locked()
            entered, release = threading.Event(), threading.Event()
            errors = []

            def create_child(*_args, **_kwargs):
                entered.set()
                release.wait(3)
                with first._lock:
                    child = ManagedRun("new-child", "new", temporary, {}, continued_from_run_id=source.run_id)
                    first._runs[child.run_id] = child
                    first._save_state_locked()
                return {"run_id": child.run_id}

            def submit():
                try:
                    first.continue_from_agent(source.run_id, 7, "new")
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=submit)
            try:
                with (mock.patch.object(first, "_continue_from_agent", side_effect=create_child),
                      mock.patch.object(second, "_continue_from_agent") as unsafe):
                    worker.start()
                    self.assertTrue(entered.wait(1))
                    with self.assertRaises(PluginRunPendingError):
                        second.continue_from_agent(source.run_id, 7, "retained", require_unstarted_continuation=True)
                    release.set()
                    worker.join(4)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(errors)
                    with self.assertRaises(PluginRunError):
                        second.continue_from_agent(source.run_id, 7, "retained", require_unstarted_continuation=True)
                    unsafe.assert_not_called()
            finally:
                release.set()
                worker.join(4)
                first.close()
                second.close()

    def test_busy_workspace_keeps_the_original_prompt_and_finishes_after_server_restart(self):
        for status in ("preparing", "running", "stopping", "finalizing"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                manager = FakeRunManager()
                row = {"run_id": "busy", "workspace": str(workspace), "status": status,
                       "worker_active": True, "agents": []}
                manager.get_run = lambda *_args, **_kwargs: dict(row)
                controller = RemoteController(state_dir=root / "state", manager=manager)
                registration = controller.registry.register(str(workspace))
                workspace_id = registration["workspace"]["workspace_id"]
                controller.registry.update_context(workspace_id, run_id="busy")
                request = RemoteRequest(action="turns.start", client_id="mac-app", command_id="original-intent",
                    body={"workspace_id": workspace_id, "prompt": "The original next question", "num_agents": 1})
                server = PcrdServer(root / "s", controller)
                try:
                    response = server.execute(request.to_mapping())
                    self.assertFalse(response["ok"])
                    # The existing client wire code preserves the original outbox
                    # even before its new workspace-busy classification is installed.
                    self.assertEqual(response["error"]["code"], "pcr_operation_pending")
                    self.assertFalse(manager.started)
                    self.assertEqual(server.prompt_receipt(request)["status"], "retryable")
                finally:
                    server.server_close()
                    controller.close()
                resumed = RemoteController(state_dir=root / "state", manager=manager)
                server = PcrdServer(root / "s", resumed)
                server.prompt_delivery.poll_seconds = 0.01
                server.prompt_delivery.retry_seconds = 0.01
                server.prompt_delivery.start()
                try:
                    receipt = server.prompt_receipt(request)
                    self.assertTrue(receipt["recovery_owned_by_daemon"])
                    row.update(status="finalized", worker_active=False, finalized_agent=7)
                    self.wait_for(lambda: server.prompt_receipt(request)["status"] == "completed")
                    self.assertEqual(len(manager.started), 1)
                    self.assertEqual(manager.started[0]["prompt"], request.body["prompt"])
                    self.assertEqual(manager.started[0]["submission_command"], CommandJournal.identity(request))
                    self.assertTrue(server.execute(request.to_mapping())["ok"])
                    self.assertEqual(len(manager.started), 1)
                finally:
                    server.server_close()
                    resumed.close()

    def test_unsafe_workspace_is_a_proven_rejection_not_an_ambiguous_or_background_prompt(self):
        for status in ("sync_ambiguous", "cleanup_failed"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                manager = FakeRunManager()
                manager.get_run = lambda *_args, **_kwargs: {
                    "run_id": "unsafe", "workspace": str(workspace), "status": status,
                    "worker_active": False, "agents": []}
                controller = RemoteController(state_dir=root / "state", manager=manager)
                registration = controller.registry.register(str(workspace))
                workspace_id = registration["workspace"]["workspace_id"]
                controller.registry.update_context(workspace_id, run_id="unsafe")
                server = PcrdServer(root / "s", controller)
                request = RemoteRequest(action="turns.start", client_id="mac-app", command_id="retained-intent",
                    body={"workspace_id": workspace_id, "prompt": "Do not bypass a save conflict", "num_agents": 1})
                try:
                    self.assertFalse(server.execute(request.to_mapping())["ok"])
                    receipt = server.prompt_receipt(request)
                    self.assertEqual(receipt["status"], "rejected")
                    self.assertEqual(receipt["error"]["delivery_outcome"], "not_applied")
                    self.assertFalse(server.journal.pending_requests())
                    self.assertFalse(manager.started or manager.continued)
                finally:
                    server.server_close()
                    controller.close()

    def test_legacy_busy_receipt_does_not_claim_a_background_owner_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError("workspace_busy",
                    "Finish or discard the current run before changing workspaces.")))
            before = (journal.directory / f"{journal._key(request)}.json").read_bytes()
            self.assertEqual(journal.lookup(request), {"status": "retryable"})
            self.assertFalse(journal.pending_requests())
            self.assertEqual(before, (journal.directory / f"{journal._key(request)}.json").read_bytes())
            result, replayed = journal.execute(request, lambda: {"run_id": "once"})
            self.assertFalse(replayed)
            self.assertEqual(result["run_id"], "once")
            journal.execute(request, lambda: self.fail("must not apply the original message twice"))

    def test_continuation_joins_save_before_selection_is_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = PluginRunManager(Path(temporary) / "state")
            run = ManagedRun("previous-turn", "old question", temporary, {"sync_back": True},
                status="stopping", results={7: {"status": "success"}},
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_operation="finalize", worker_state="busy")
            try:
                with (mock.patch.object(manager, "_require_run_locked", return_value=run),
                      mock.patch.object(manager, "_worker_active_locked", return_value=True),
                      mock.patch.object(manager, "_require_agent_workspace_for_action"),
                      mock.patch.object(manager, "_require_persistent_worker_locked"),
                      mock.patch.object(manager, "estimate", return_value={"confirmation_required": False}),
                      mock.patch.object(manager, "stop_run") as stop,
                      mock.patch.object(manager, "accept_agent") as accept,
                      mock.patch.object(manager, "start_run") as start):
                    with self.assertRaises(PluginRunPendingError):
                        manager.continue_from_agent(run.run_id, 7, "original next question")
                    stop.assert_not_called()
                    accept.assert_not_called()
                    start.assert_not_called()
            finally:
                manager.close()

    def test_stop_cannot_interrupt_a_result_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = PluginRunManager(Path(temporary) / "state")
            run = ManagedRun("previous-turn", "old question", temporary, {}, status="completed",
                worker_protocol=WORKER_PROTOCOL_VERSION, worker_operation="finalize", worker_state="busy")
            try:
                with (mock.patch.object(manager, "_require_run_locked", return_value=run),
                      mock.patch.object(manager, "_worker_active_locked", return_value=True),
                      mock.patch.object(manager, "_save_state_locked") as save):
                    with self.assertRaises(PluginRunPendingError):
                        manager.stop_run(run.run_id, wait_seconds=0)
                    self.assertEqual(run.status, "completed")
                    save.assert_not_called()
            finally:
                manager.close()

    def test_selected_result_is_reserved_before_worker_can_preserve_diffs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = PluginRunManager(Path(temporary) / "state")
            run = ManagedRun("previous-turn", "old question", temporary, {}, status="completed",
                worker_protocol=WORKER_PROTOCOL_VERSION, results={7: {"status": "success"}})
            def queue(value, operation, body):
                self.assertEqual((value.status, value.finalizing_agent, value.finalized_agent), ("finalizing", 7, None))
                self.assertEqual((operation, body), ("finalize", {"agent": 7}))
                return "save-operation"
            try:
                with (mock.patch.object(manager, "_require_run_locked", return_value=run),
                      mock.patch.object(manager, "_worker_active_locked", return_value=False),
                      mock.patch.object(manager, "_require_agent_workspace_for_action"),
                      mock.patch.object(manager, "_require_persistent_worker_locked"),
                      mock.patch.object(manager, "_queue_persistent_operation_locked", side_effect=queue),
                      mock.patch.object(manager, "_wait_for_persistent_operation", return_value=None),
                      mock.patch.object(manager, "_run_summary_locked", return_value={"status": "finalizing"})):
                    self.assertTrue(manager._accept_agent_via_worker(run.run_id, 7, 0)["operation_pending"])
            finally:
                manager.close()

    def request(self, command_id="original"):
        return RemoteRequest(action="turns.continue", client_id="mac-app", command_id=command_id,
            body={"run_id": "previous-turn", "agent": 7, "prompt": "Keep this original question",
                  "num_agents": 1, "confirm_large_run": True})

    def wait_for(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Durable handoff did not advance")

    def test_unrelated_commands_do_not_wait_behind_a_long_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            entered, release, other_finished = threading.Event(), threading.Event(), threading.Event()
            def saving():
                entered.set()
                release.wait(4)
                return {"status": "finalized"}
            first = threading.Thread(target=lambda: journal.execute(self.request(), saving))
            second = threading.Thread(target=lambda: (
                journal.execute(self.request("another-workspace"), lambda: {"run_id": "independent"}),
                other_finished.set()))
            first.start()
            try:
                self.assertTrue(entered.wait(1))
                second.start()
                self.assertTrue(other_finished.wait(0.3), "A different command inherited the save's lock")
            finally:
                release.set()
                first.join(5)
                second.join(5)

    def test_direct_and_queued_handoffs_share_workspace_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root, manager=manager)
            server = PcrdServer(root / "s", controller)
            entered, release = threading.Event(), threading.Event()
            def handoff(request):
                if request.command_id == "original":
                    entered.set()
                    release.wait(4)
                return {"run_id": "private-result"}
            controller._actions["turns.continue"] = handoff
            def get_run(run_id):
                return {"workspace": str(root / ("other" if run_id == "other-source" else "same"))}
            worker = threading.Thread(target=lambda: server.execute(self.request().to_mapping()))
            with mock.patch.object(manager, "get_run", side_effect=get_run, create=True):
                worker.start()
                try:
                    self.assertTrue(entered.wait(1))
                    queued = RemoteRequest(action="turns.continue", client_id="pcrd-follow-up",
                        command_id="queued-original", body=self.request().body)
                    busy = server.execute(queued.to_mapping())
                    self.assertEqual(busy["error"]["code"], "pcr_operation_pending")
                    independent = RemoteRequest(action="turns.continue", client_id="another-client",
                        command_id="independent", body={**self.request().body, "run_id": "other-source"})
                    self.assertTrue(server.execute(independent.to_mapping())["ok"])
                    health = controller._health(self.request())
                    self.assertEqual(health["prompt_admission_count"], 1)
                    self.assertEqual(health["pcr"]["active_operation"], "prompt_delivery")
                finally:
                    release.set()
                    worker.join(5)
                    server.server_close()
                    controller.close()

    def test_pending_original_survives_restart_and_advances_without_a_client(self):
        from parallel_codex_runner_core.remote.prompt_delivery import PromptDeliveryCoordinator
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = CommandJournal(root / "commands")
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError(
                    "pcr_operation_pending", "saving", retryable=True)))
            record = json.loads((journal.directory / f"{journal._key(request)}.json").read_text())
            self.assertEqual(RemoteRequest.from_mapping(record["request"]).body, request.body)
            self.assertNotIn("auth_token", record["request"])
            restored = CommandJournal(journal.directory)
            self.assertEqual(restored.pending_requests()[0].command_id, request.command_id)
            ready, applied = threading.Event(), []
            def execute(original):
                def accept():
                    if not ready.is_set():
                        raise RemoteControllerError("pcr_operation_pending", "saving", retryable=True)
                    applied.append(CommandJournal.identity(original))
                    return {"status": "started", "run_id": "new-turn"}
                return restored.execute(original, accept)
            coordinator = PromptDeliveryCoordinator(restored, execute, poll_seconds=0.01, retry_seconds=0.01)
            coordinator.start()
            try:
                self.assertEqual(restored.lookup(request)["delivery_state"], "waiting_previous_turn")
                self.assertEqual(coordinator.status()["pending_count"], 1)
                ready.set()
                self.wait_for(lambda: restored.lookup(request)["status"] == "completed")
                self.assertEqual(applied, [CommandJournal.identity(request)])
                restored.execute(request, lambda: self.fail("duplicate application"))
                self.wait_for(lambda: not coordinator.status()["active_count"])
            finally:
                coordinator.stop()

    def test_background_server_handoff_keeps_source_settings_and_receipt_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root, manager=manager)
            server = PcrdServer(root / "s", controller)
            request = self.request()
            saving = threading.Event()
            saving.set()
            original_continue = manager.continue_from_agent
            def continue_after_save(*args, **kwargs):
                if saving.is_set():
                    raise PluginRunPendingError("The original result is still saving")
                return original_continue(*args, **kwargs)
            with mock.patch.object(manager, "continue_from_agent", side_effect=continue_after_save):
                server.prompt_delivery.poll_seconds = 0.01
                server.prompt_delivery.retry_seconds = 0.01
                server.prompt_delivery.start()
                try:
                    pending = server.execute(request.to_mapping())
                    self.assertFalse(pending["ok"])
                    self.assertEqual(pending["error"]["code"], "pcr_operation_pending")
                    receipt = server.prompt_receipt(request)
                    self.assertTrue(receipt["recovery_owned_by_daemon"])
                    self.assertIn(receipt["status"], {"retryable", "processing"})
                    self.assertFalse(manager.continued)
                    saving.clear()
                    self.wait_for(lambda: server.prompt_receipt(request)["status"] == "completed")
                    self.assertEqual(len(manager.continued), 1)
                    accepted = manager.continued[0]
                    self.assertEqual(accepted["submission_command"], CommandJournal.identity(request))
                    self.assertEqual(accepted["run_id"], request.body["run_id"])
                    self.assertEqual(accepted["agent"], 7)
                    self.assertEqual(accepted["prompt"], request.body["prompt"])
                    self.assertTrue(accepted["confirm_large_run"])
                finally:
                    server.server_close()
                    controller.close()

    def test_unknown_failure_and_altered_envelopes_are_not_background_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(
                    RemoteControllerError("internal_error", "unproven side effects")))
            self.assertFalse(journal.pending_requests())
            path = journal.directory / f"{journal._key(request)}.json"
            record = json.loads(path.read_text())
            record["status"] = "retryable"
            record["request"]["body"]["agent"] = 8
            journal._write(path, record)
            self.assertFalse(journal.pending_requests())

    def test_legacy_stop_timeout_requires_committed_completed_save_and_no_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = PluginRunManager(root / "state")
            journal = CommandJournal(root / "commands")
            request = self.request()
            path = journal.directory / f"{journal._key(request)}.json"
            journal._write(path, {**journal.identity(request), "action": request.action, "status": "failed",
                "error": {"type": "RemoteControllerError", "code": "pcr_error",
                    "message": "Timed out while stopping remaining Agents; try again after the run stops"}})
            source = ManagedRun("previous-turn", "old question", temporary, {"sync_back": True},
                status="finalized", finalized_agent=7, synced_back=True,
                session_promotion={"session_id": "promoted-original-session"})
            def proof(original):
                return manager.can_retry_unstarted_continuation(original.body["run_id"], original.body["agent"])
            before = path.read_bytes()
            try:
                manager._runs[source.run_id] = source
                self.assertEqual(journal.lookup(request, can_retry=proof)["status"], "in_doubt")
                manager._save_state_locked()
                self.assertEqual(journal.lookup(request, can_retry=proof)["status"], "retryable")
                self.assertEqual(before, path.read_bytes())
                source.finalized_agent = 8
                manager._save_state_locked()
                self.assertEqual(journal.lookup(request, can_retry=proof)["status"], "in_doubt")
                source.finalized_agent = 7
                manager._runs["child"] = ManagedRun("child", "another turn", temporary, {},
                    continued_from_run_id=source.run_id, continued_from_agent=7)
                manager._save_state_locked()
                self.assertEqual(journal.lookup(request, can_retry=proof)["status"], "in_doubt")
            finally:
                manager.close()

    def test_pending_envelope_is_not_pruned_as_an_old_failed_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            with self.assertRaises(RemoteControllerError):
                journal.execute(self.request(), lambda: (_ for _ in ()).throw(
                    RemoteControllerError("pcr_operation_pending", "saving", retryable=True)))
            self.assertEqual(journal.prune(now=time.time() + 31 * 24 * 3600), 0)
            self.assertEqual(len(journal.pending_requests()), 1)

    def test_pending_index_cleanup_rechecks_the_committed_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(
                    RemoteControllerError("pcr_operation_pending", "saving", retryable=True)))
            path = journal.directory / f"{journal._key(request)}.json"
            record = journal._read(path)
            # A scan's stale terminal snapshot cannot erase a newer pending
            # attempt committed before it acquires the command lock.
            with mock.patch.object(journal, "_read", side_effect=[{**record, "status": "failed"}, record]):
                self.assertEqual(journal.pending_requests(), [])
            self.assertEqual(journal.pending_requests(), [request])
