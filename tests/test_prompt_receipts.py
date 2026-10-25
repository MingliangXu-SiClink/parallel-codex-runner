import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.plugin_runtime import PluginRunError, PluginRunManager
from parallel_codex_runner_core.remote.controller import RemoteController, RemoteControllerError
from parallel_codex_runner_core.remote.ipc import PcrdServer
from parallel_codex_runner_core.remote.journal import CommandJournal, CommandJournalError
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from .test_remote import FakeRunManager


class PromptReceiptTests(unittest.TestCase):
    def request(self, **kwargs):
        return RemoteRequest(action="turns.continue", client_id="mac-app",
                             command_id="saved-message", body={"run_id": "pcr-source", "agent": 1}, **kwargs)

    def test_lookup_does_not_execute_or_write_and_is_scoped_to_exact_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            journal.execute(request, lambda: {"status": "started", "run_id": "pcr-result"})
            before = {p.name: p.read_bytes() for p in Path(temporary).glob("*.json")}
            self.assertEqual(journal.lookup(request)["result"]["run_id"], "pcr-result")
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(temporary).glob("*.json")})
            other = RemoteRequest(action=request.action, client_id="other", command_id=request.command_id,
                                  body=request.body)
            self.assertEqual(journal.lookup(other), {"status": "not_found"})
            changed = RemoteRequest(action=request.action, client_id=request.client_id,
                                    command_id=request.command_id, body={"run_id": "different"})
            with self.assertRaises(CommandJournalError):
                journal.lookup(changed)

    def test_lookup_does_not_wait_for_an_inflight_command_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            entered, release = threading.Event(), threading.Event()
            def callback():
                entered.set()
                release.wait(5)
                return {"run_id": "pcr-result"}
            worker = threading.Thread(target=lambda: journal.execute(self.request(), callback))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(journal.lookup(self.request())["status"], "in_doubt")
            finally:
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())

    def test_accepted_run_recovers_a_failed_receipt_without_starting_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = PluginRunManager(root / "state")
            journal = CommandJournal(root / "commands")
            request = self.request()
            try:
                accepted_run_id = "pcr-20261004T000000Z-12345678"
                def callback():
                    manager._runs[accepted_run_id] = ManagedRun(run_id=accepted_run_id, prompt="saved",
                        workspace=temporary, config={"submission_command": journal.identity(request)},
                        continued_from_run_id="pcr-source", continued_from_agent=1)
                    manager._save_state_locked()
                    raise RemoteControllerError("internal_error", "lost after acceptance")
                def recover(value):
                    return manager.prompt_command_result(journal.identity(value))
                with self.assertRaises(RemoteControllerError):
                    journal.execute(request, callback, recover)
                before = (root / "commands" / f"{journal._key(request)}.json").read_bytes()
                receipt = journal.lookup(request, recover)
                self.assertEqual(receipt["status"], "completed")
                self.assertEqual(receipt["result"]["run_id"], accepted_run_id)
                self.assertEqual(before, (root / "commands" / f"{journal._key(request)}.json").read_bytes())
                with mock.patch.object(manager, "start_run", side_effect=AssertionError("duplicate")):
                    result, replayed = journal.execute(request, lambda: self.fail("reexecuted"), recover)
                self.assertTrue(replayed)
                self.assertEqual(result["run_id"], accepted_run_id)
                self.assertEqual(len(manager._runs), 1)
            finally:
                manager.close()

    def test_completed_receipt_write_failure_recovers_from_committed_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            accepted = {}
            write = journal._write
            def broken_write(path, value):
                if value.get("status") == "completed":
                    raise OSError("fixture disk full after acceptance")
                write(path, value)
            def callback():
                accepted.update({"status": "started", "run_id": "pcr-result"})
                return dict(accepted)
            with mock.patch.object(journal, "_write", side_effect=broken_write):
                with self.assertRaises(OSError):
                    journal.execute(request, callback)
            self.assertEqual(journal.lookup(request, lambda _: dict(accepted))["result"]["run_id"], "pcr-result")
            result, replayed = journal.execute(request, lambda: self.fail("duplicate"), lambda _: dict(accepted))
            self.assertTrue(replayed)
            self.assertEqual(result["run_id"], "pcr-result")

    def test_recovery_uses_only_committed_state_and_rejects_ambiguous_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = PluginRunManager(root / "state")
            journal = CommandJournal(root / "commands")
            identity = journal.identity(self.request())
            try:
                first_id = "pcr-20261004T000000Z-12345678"
                manager._runs[first_id] = ManagedRun(first_id, "saved", temporary,
                    {"submission_command": identity})
                self.assertIsNone(manager.prompt_command_result(identity))
                manager._save_state_locked()
                self.assertEqual(manager.prompt_command_result(identity)["run_id"], first_id)
                second_id = "pcr-20261004T000001Z-12345678"
                manager._runs[second_id] = ManagedRun(second_id, "saved", temporary,
                    {"submission_command": identity})
                manager._save_state_locked()
                with self.assertRaises(PluginRunError):
                    manager.prompt_command_result(identity)
                manager.state_path.write_text("not valid JSON")
                with self.assertRaises(ValueError):
                    manager.prompt_command_result(identity)
            finally:
                manager.close()

    def test_generic_failure_is_never_proof_of_rejection_or_safe_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, lambda: (_ for _ in ()).throw(RemoteControllerError("internal_error", "failed")))
            self.assertEqual(journal.lookup(request), {"status": "in_doubt"})
            with self.assertRaises(CommandJournalError):
                journal.execute(request, lambda: self.fail("unsafe retry"))

    def test_preflight_rejection_retains_original_reason_and_proves_not_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            with self.assertRaises(RemoteControllerError):
                journal.execute(self.request(), lambda: (_ for _ in ()).throw(RemoteControllerError(
                    "finalization_blocked", "result needs safe recovery", details={"delivery_outcome": "not_applied"})))
            receipt = journal.lookup(self.request())
            self.assertEqual(receipt["status"], "rejected")
            self.assertEqual(receipt["error"]["code"], "finalization_blocked")

    def test_only_the_exact_legacy_preflight_guard_is_recoverable_with_same_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            path = journal.directory / f"{journal._key(request)}.json"
            record = {**journal.identity(request), "action": request.action, "status": "failed",
                      "error": {"type": "RemoteControllerError", "message": "Resolve the previous finalization before continuing"}}
            journal._write(path, record)
            self.assertEqual(journal.lookup(request), {"status": "retryable"})
            result, replayed = journal.execute(request, lambda: {"run_id": "pcr-result"})
            self.assertFalse(replayed)
            self.assertEqual(result["run_id"], "pcr-result")
            record["error"]["message"] += " (not exact)"
            journal._write(path, record)
            self.assertEqual(journal.lookup(request), {"status": "in_doubt"})

    def test_legacy_retry_permission_is_consumed_before_a_new_attempt_can_have_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = CommandJournal(Path(temporary))
            request = self.request()
            path = journal.directory / f"{journal._key(request)}.json"
            journal._write(path, {**journal.identity(request), "action": request.action, "status": "failed",
                "error": {"type": "RemoteControllerError", "message": "Resolve the previous finalization before continuing"}})
            def retry():
                self.assertEqual(journal.lookup(request), {"status": "in_doubt"})
                raise RemoteControllerError("internal_error", "new attempt is not the old preflight")
            with self.assertRaises(RemoteControllerError):
                journal.execute(request, retry)
            self.assertEqual(journal.lookup(request), {"status": "in_doubt"})
            with self.assertRaises(CommandJournalError):
                journal.execute(request, lambda: self.fail("old retry permission reused"))

    def test_a_new_server_reconciles_the_old_failed_continuation_then_applies_it_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = RemoteRequest(action="turns.continue", client_id="mac-app",
                command_id="saved-message-attempt-0-confirmed", body={
                    "run_id": "pcr-source", "agent": 7, "prompt": "A retained question",
                    "num_agents": 1, "confirm_large_run": True})
            commands = root / "commands"
            commands.mkdir()
            path = commands / f"{CommandJournal._key(request)}.json"
            legacy = {**CommandJournal.identity(request), "action": request.action, "status": "failed",
                "error": {"type": "RemoteControllerError",
                          "message": "Resolve the previous finalization before continuing"}}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            before = path.read_bytes()
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root, manager=manager)
            server = PcrdServer(root / "s", controller)
            query = RemoteRequest(action="commands.get", client_id="mac-app", body={
                "command_id": request.command_id, "action": request.action, "body": request.body})
            try:
                self.assertTrue(server.execute(RemoteRequest(action="system.capabilities").to_mapping())[
                    "body"]["supports_prompt_receipts"])
                receipt = server.execute(query.to_mapping())
                self.assertTrue(receipt["ok"])
                self.assertEqual(receipt["body"]["status"], "retryable")
                self.assertEqual(before, path.read_bytes())
                self.assertFalse(manager.continued or manager.started)

                accepted = server.execute(request.to_mapping())
                self.assertTrue(accepted["ok"], accepted)
                self.assertEqual(accepted["body"]["run_id"], "pcr-next-run")
                self.assertEqual(len(manager.continued), 1)
                applied = manager.continued[0]
                self.assertEqual(applied["run_id"], "pcr-source")
                self.assertEqual(applied["agent"], 7)
                self.assertEqual(applied["submission_command"], CommandJournal.identity(request))
                self.assertTrue(applied["confirm_large_run"])
                completed = server.execute(query.to_mapping())
                self.assertEqual(completed["body"]["status"], "completed")
                self.assertEqual(completed["body"]["result"]["run_id"], "pcr-next-run")
                replayed = server.execute(request.to_mapping())
                self.assertTrue(replayed["ok"])
                self.assertEqual(replayed["body"]["run_id"], "pcr-next-run")
                self.assertEqual(len(manager.continued), 1)
            finally:
                server.server_close()
                controller.close()

    def test_journal_identity_survives_restart_but_not_a_different_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = CommandJournal(root / "one")
            self.assertEqual(first.store_id, CommandJournal(root / "one").store_id)
            self.assertNotEqual(first.store_id, CommandJournal(root / "two").store_id)

    def test_protocol_query_is_read_only_and_not_a_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root, manager=manager)
            server = PcrdServer(root / "s", controller)
            try:
                original = self.request()
                server.journal.execute(original, lambda: {"status": "started", "run_id": "pcr-result"})
                query = RemoteRequest(action="commands.get", client_id="mac-app", body={
                    "command_id": original.command_id, "action": original.action, "body": original.body})
                response = server.execute(query.to_mapping())
                self.assertTrue(response["ok"])
                self.assertEqual(response["body"]["command_id"], original.command_id)
                self.assertEqual(response["body"]["receipt_store_id"], server.journal.store_id)
                self.assertEqual(response["body"]["result"]["run_id"], "pcr-result")
                self.assertFalse(manager.started or manager.continued)
                self.assertIn("commands.get", controller.READ_ONLY_ACTIONS)
                self.assertNotIn("commands.get", controller.MUTATING_ACTIONS)
                self.assertFalse((server.journal.directory / f"{server.journal._key(query)}.json").exists())
            finally:
                server.server_close()
                controller.close()
