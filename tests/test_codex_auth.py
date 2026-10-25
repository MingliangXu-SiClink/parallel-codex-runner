import asyncio
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parallel_codex_runner_core import app, codex_app_server as adapter
from parallel_codex_runner_core.codex_auth import (SOURCE_ENV, changed_auth_for_block, file_auth,
                                                  install_for_start, selected_home, supports_file_auth)
from parallel_codex_runner_core.codex_usage import CodexUsageReader
from scripts.verify_account_follow_native import fixture_auth
from tests.test_agent_execution_recovery import NativePeer


class FileAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.private = self.root / "source", self.root / "private"
        self.source.mkdir()
        self.private.mkdir()
        self.environment = patch.dict(os.environ, {SOURCE_ENV: str(self.source), "CODEX_HOME": str(self.private)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write(self.source, "a")
        self.write(self.private, "b")

    def write(self, home, account, revision=1):
        (home / "auth.json").write_text(json.dumps(fixture_auth(account, revision)))

    def test_stable_identity_changes_with_user_not_token_refresh_or_plan(self):
        first = file_auth(self.source)
        self.write(self.source, "a", 2)
        second = file_auth(self.source)
        self.assertEqual(first.identity, second.identity)
        self.assertNotEqual(first.access_token, second.access_token)
        self.assertNotIn("synthetic", repr(first))
        self.assertNotIn(first.access_token, repr(first))
        self.write(self.source, "b")
        self.assertNotEqual(first.identity, file_auth(self.source).identity)

    def test_same_workspace_different_user_invalidates_usage_but_token_renewal_does_not(self):
        (self.source / "auth.json").write_text(json.dumps(fixture_auth("workspace", user="first-user")))
        first = CodexUsageReader.source_id("codex", self.source)
        (self.source / "auth.json").write_text(json.dumps(fixture_auth("workspace", 2, user="first-user")))
        self.assertEqual(CodexUsageReader.source_id("codex", self.source), first)
        (self.source / "auth.json").write_text(json.dumps(fixture_auth("workspace", user="second-user")))
        self.assertNotEqual(CodexUsageReader.source_id("codex", self.source), first)

    def test_native_start_replaces_stale_private_auth_but_not_original_history(self):
        original = self.private / "sessions"
        original.mkdir()
        (original / "original.jsonl").write_text("saved original history")
        installed = install_for_start(self.source, self.private)
        self.assertEqual(file_auth(self.private).identity, installed.identity)
        self.assertEqual((original / "original.jsonl").read_text(), "saved original history")
        self.assertEqual(stat.S_IMODE((self.private / "auth.json").stat().st_mode), 0o600)
        self.assertEqual(list(self.private.glob(".pcr-auth-*")), [])

    def test_keyring_auto_api_and_custom_providers_are_not_mistaken_for_file_identity(self):
        for config in ('cli_auth_credentials_store="keyring"', 'cli_auth_credentials_store="auto"',
                       'model_provider="custom"', 'profile="custom"', '[model_providers.openai]\nname="override"'):
            with self.subTest(config=config):
                (self.source / "config.toml").write_text(config)
                self.assertFalse(supports_file_auth(self.source))
                self.assertIsNone(file_auth(self.source))
        (self.source / "config.toml").unlink()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic"}):
            self.assertIsNone(file_auth(self.source))

    def test_copy_refuses_symlink_and_source_change_before_publication(self):
        (self.private / "auth.json").unlink()
        (self.private / "auth.json").symlink_to(self.source / "auth.json")
        self.assertIsNone(install_for_start(self.source, self.private))
        (self.private / "auth.json").unlink()
        self.write(self.private, "b")
        before = (self.private / "auth.json").read_bytes()
        first = file_auth(self.source)
        self.write(self.source, "c")
        newer = file_auth(self.source)
        with patch("parallel_codex_runner_core.codex_auth.file_auth", side_effect=[first, newer]):
            self.assertIsNone(install_for_start(self.source, self.private))
        self.assertEqual((self.private / "auth.json").read_bytes(), before)

    def test_logical_source_path_follows_switcher_symlink(self):
        link = self.root / "selected"
        link.symlink_to(self.source)
        with patch.dict(os.environ, {SOURCE_ENV: str(link)}):
            self.assertEqual(selected_home(), link)
            old = file_auth(selected_home()).identity
            link.unlink()
            link.symlink_to(self.private)
            self.assertNotEqual(file_auth(selected_home()).identity, old)
            env = app.agent_process_environment(self.root, self.private)
            self.assertEqual(env[SOURCE_ENV], str(link))

    def test_old_auth_failure_resumes_only_after_a_verified_identity_change(self):
        state = {"error": {"codexErrorInfo": "unauthorized"}}
        self.assertTrue(changed_auth_for_block(state, self.private))
        state["auth"] = {"identity": file_auth(self.source).identity}
        self.assertFalse(changed_auth_for_block(state, self.private))
        state["error"] = {"codexErrorInfo": "other", "message": "Please sign in"}
        self.assertFalse(changed_auth_for_block(state, self.private))


class AccountPeer(NativePeer):
    def __init__(self, history, requests, source, account, *, rapid=False, stop=None, reject=False):
        super().__init__(history, lambda: "success", requests)
        self.source, self.rapid, self.stop, self.reject = source, rapid, stop, reject
        self.account = account
        self.starts = 0

    def finish(self):
        self.history["turn"]["status"] = "completed"
        self.emit({"method": "item/completed", "params": {"threadId": "private-thread", "turnId": "original-turn",
            "item": {"id": "final", "type": "agentMessage", "phase": "final_answer", "text": "original work retained"}}})
        self.emit({"method": "turn/completed", "params": {"threadId": "private-thread", "turn": self.history["turn"]}})

    def write(self, raw):
        value = json.loads(raw)
        method, identifier = value.get("method"), value.get("id")
        if method == "account/read":
            self.requests.append(value)
            account = self.account
            if account == "b" and self.stop:
                self.stop.write_text("user stop")
            if account == "b" and self.reject:
                self.emit({"id": identifier, "error": {"code": -32602, "message": "synthetic secret must not be logged"}})
                return
            if self.rapid and account == "b":
                (self.source / "auth.json").write_text(json.dumps(fixture_auth("c")))
            self.emit({"id": identifier, "result": {"requiresOpenaiAuth": True, "account": {"type": "chatgpt"},
                "workspaceRouting": {"chatgptAccountId": account}}})
            return
        if method in {"thread/backgroundTerminals/list", "thread/loaded/list"}:
            self.requests.append(value)
            self.emit({"id": identifier, "result": {"data": [] if method == "thread/backgroundTerminals/list" else ["private-thread"]}})
            return
        if method == "turn/interrupt":
            self.requests.append(value)
            self.emit({"id": identifier, "result": {}})
            self.history["turn"]["status"] = "interrupted"
            self.emit({"method": "turn/completed", "params": {"threadId": "private-thread", "turn": self.history["turn"]}})
            return
        if method == "turn/start":
            if self.history.get("turn"):
                self.starts += 1
                super().write(raw)
                return
            self.requests.append(value)
            self.starts += 1
            self.history.update(turn={"id": "original-turn", "status": "inProgress"}, items=[])
            self.emit({"id": identifier, "result": {"turn": self.history["turn"]}})
            self.emit({"method": "turn/started", "params": {"threadId": "private-thread", "turn": self.history["turn"]}})
            self.emit({"method": "item/started", "params": {"threadId": "private-thread", "turnId": "original-turn",
                "item": {"id": "original-job", "type": "commandExecution", "status": "inProgress"}}})
            (self.source / "auth.json").write_text(json.dumps(fixture_auth("b")))
            asyncio.get_running_loop().call_later(.05, self.emit, {"method": "item/completed", "params": {
                "threadId": "private-thread", "turnId": "original-turn", "item": {
                    "id": "original-job", "type": "commandExecution", "status": "completed", "exitCode": 0}}})
            return
        super().write(raw)


class AccountFollowingTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, *, rapid=False, stopped=False, reject=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, private = root / "source", root / "private"
            source.mkdir()
            private.mkdir()
            (source / "auth.json").write_text(json.dumps(fixture_auth("a")))
            output = root / "final.md"
            requests, history = [], {}
            peers = []
            async def spawn_native(*_args, **_kwargs):
                peer = AccountPeer(history, requests, source, file_auth(private).account, rapid=rapid,
                                   stop=root / "execution_stop" if stopped else None, reject=reject)
                peers.append(peer)
                return peer.process
            with patch.dict(os.environ, {SOURCE_ENV: str(source), "CODEX_HOME": str(private)}, clear=True), \
                 patch.object(adapter.asyncio, "create_subprocess_exec", side_effect=spawn_native) as spawn, \
                 contextlib.redirect_stdout(io.StringIO()) as emitted:
                if stopped or reject:
                    with self.assertRaises(asyncio.CancelledError if stopped else adapter.AppServerError):
                        await asyncio.wait_for(adapter.run(["codex", "exec", "-o", str(output), "-"],
                            "original task", automatic_recovery=True), 5)
                else:
                    self.assertEqual(await asyncio.wait_for(adapter.run(["codex", "exec", "-o", str(output), "-"],
                        "original task", automatic_recovery=True), 5), 0)
            state = json.loads((root / "execution_recovery.json").read_text())
            self.assertEqual(spawn.await_count, 3 if rapid else 2)
            self.assertEqual(sum(peer.starts for peer in peers), 1 if stopped or reject else 2)
            self.assertNotIn("synthetic secret", emitted.getvalue())
            self.assertEqual(len([value for value in requests if value.get("method") == "turn/interrupt"]), 1)
            self.assertEqual(len([value for value in requests if value.get("method") == "thread/start"]), 1)
            self.assertFalse(any(value.get("method") in {"account/login/start", "account/logout"} for value in requests))
            return state, requests

    async def test_changes_native_account_via_original_thread_without_restarting_tools(self):
        state, requests = await self.execute()
        self.assertEqual(state["auth"]["status"], "current")
        self.assertEqual(state["stage"], "completed")
        self.assertEqual(len([x for x in requests if x.get("method") == "thread/resume"]), 1)

    async def test_late_b_ack_cannot_be_presented_as_current_c_or_start_an_extra_turn(self):
        state, requests = await self.execute(rapid=True)
        self.assertEqual(state["auth"]["status"], "current")
        self.assertEqual(len([x for x in requests if x.get("method") == "thread/resume"]), 1)

    async def test_user_stop_wins_over_inflight_account_change(self):
        state, requests = await self.execute(stopped=True)
        self.assertEqual(state["stage"], "stopped")
        self.assertEqual(len([x for x in requests if x.get("method") == "turn/start"]), 1)

    async def test_rejected_change_is_not_claimed_as_current_and_does_not_replay_the_task(self):
        state, _ = await self.execute(reject=True)
        self.assertEqual(state["auth"]["status"], "blocked")

    async def test_missing_selected_credentials_cannot_restart_under_stale_private_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            private.mkdir()
            (private / "auth.json").write_text(json.dumps(fixture_auth("old")))
            with patch.dict(os.environ, {SOURCE_ENV: str(root / "missing"), "CODEX_HOME": str(private)}, clear=True), \
                 patch.object(adapter.asyncio, "create_subprocess_exec") as spawn, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(adapter.AppServerError):
                    await adapter.run(["codex", "exec", "-o", str(root / "final.md"), "-"], "task", automatic_recovery=True)
                spawn.assert_not_called()

    async def test_unknown_or_still_active_native_turn_cannot_be_closed_for_an_account_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, private = root / "source", root / "private"
            source.mkdir()
            private.mkdir()
            (source / "auth.json").write_text(json.dumps(fixture_auth("a")))
            output, stop = root / "final.md", root / "execution_stop"
            history, requests = {}, []

            class UnsettledPeer(AccountPeer):
                def write(self, raw):
                    value = json.loads(raw)
                    if value.get("method") == "thread/turns/list":
                        self.requests.append(value)
                        self.emit({"id": value["id"], "result": {"data": [{"id": "unreconciled-turn", "status": "inProgress"}]}})
                        asyncio.get_running_loop().call_later(.05, stop.write_text, "user stop")
                        return
                    super().write(raw)

            peer = UnsettledPeer(history, requests, source, "a")
            with patch.dict(os.environ, {SOURCE_ENV: str(source), "CODEX_HOME": str(private)}, clear=True), \
                 patch.object(adapter.asyncio, "create_subprocess_exec", return_value=peer.process) as spawn, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(adapter.run(["codex", "exec", "-o", str(output), "-"],
                        "original task", automatic_recovery=True), 5)
                self.assertEqual(file_auth(private).account, "a")
            self.assertEqual(spawn.await_count, 1)
            self.assertEqual(len([request for request in requests if request.get("method") == "turn/start"]), 1)


if __name__ == "__main__":
    unittest.main()
