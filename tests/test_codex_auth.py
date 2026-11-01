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

from parallel_codex_runner_core import app, codex_app_server as adapter, codex_auth
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

    def test_legacy_token_store_preserves_identity_and_recovers_original_account_boundary(self):
        expected = file_auth(self.source)
        previous = file_auth(self.private)
        history = self.private / "sessions"
        history.mkdir()
        (history / "original.jsonl").write_text("original thread and tool outcomes")
        for null_mode in (False, True):
            with self.subTest(null_mode=null_mode):
                value = fixture_auth("a")
                if null_mode:
                    value["auth_mode"] = None
                else:
                    value.pop("auth_mode")
                    value.pop("OPENAI_API_KEY")
                source_bytes = json.dumps(value).encode()
                (self.source / "auth.json").write_bytes(source_bytes)
                self.assertEqual(file_auth(self.source).identity, expected.identity)
                blocked = {"error": {"codexErrorInfo": "unauthorized"},
                           "auth": {"identity": previous.identity}}
                self.assertTrue(changed_auth_for_block(blocked, self.private))
                installed = install_for_start(self.source, self.private)
                self.assertEqual(installed.identity, expected.identity)
                self.assertEqual((self.source / "auth.json").read_bytes(), source_bytes)
                self.assertEqual((history / "original.jsonl").read_text(), "original thread and tool outcomes")

    def test_legacy_inference_rejects_conflicting_auth_and_incomplete_token_identity(self):
        for change in ("apikey", "provider", "account", "access", "id-token", "tokens"):
            with self.subTest(change=change):
                value = fixture_auth("a")
                value.pop("auth_mode")
                if change == "apikey":
                    value["OPENAI_API_KEY"] = "synthetic-api-key"
                elif change == "provider":
                    value["auth_mode"] = "apikey"
                elif change == "account":
                    value["tokens"]["account_id"] = "different-workspace"
                elif change == "access":
                    value["tokens"]["access_token"] = ""
                elif change == "id-token":
                    value["tokens"]["id_token"] = "invalid"
                else:
                    value["tokens"] = {}
                (self.source / "auth.json").write_text(json.dumps(value))
                self.assertIsNone(file_auth(self.source))

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

    def test_unchanged_config_is_parsed_once_but_auth_is_never_cached(self):
        (self.source / "config.toml").write_text('cli_auth_credentials_store="file"')
        with patch.object(codex_auth.tomllib, "loads", wraps=codex_auth.tomllib.loads) as parse:
            for _ in range(5):
                self.assertTrue(supports_file_auth(self.source))
                self.assertEqual(file_auth(self.source).account, "a")
            self.write(self.source, "new-account")
            self.assertEqual(file_auth(self.source).account, "new-account")
            self.assertEqual(parse.call_count, 1)

    def test_cached_config_rechecks_environment_and_managed_files(self):
        (self.source / "config.toml").write_text('cli_auth_credentials_store="file"')
        self.assertTrue(supports_file_auth(self.source))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture"}):
            self.assertFalse(supports_file_auth(self.source))
        self.assertTrue(supports_file_auth(self.source))
        managed = self.source / "managed_config.toml"
        managed.touch()
        self.assertFalse(supports_file_auth(self.source))
        managed.unlink()
        self.assertTrue(supports_file_auth(self.source))

    def test_cached_config_detects_in_place_changes_and_atomic_replacement(self):
        path = self.source / "config.toml"
        allowed = 'cli_auth_credentials_store="file"'
        denied = 'cli_auth_credentials_store="auto"'
        path.write_text(allowed)
        self.assertTrue(supports_file_auth(self.source))
        initial = path.stat()
        path.write_text(denied)
        os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        self.assertFalse(supports_file_auth(self.source))
        replacement = self.source / "next-config.toml"
        replacement.write_text(allowed)
        os.utime(replacement, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        replacement.replace(path)
        self.assertTrue(supports_file_auth(self.source))
        path.write_text("invalid [toml")
        self.assertFalse(supports_file_auth(self.source))
        path.unlink()
        self.assertTrue(supports_file_auth(self.source))

    def test_cached_config_follows_logical_home_switch(self):
        (self.source / "config.toml").write_text('cli_auth_credentials_store="file"')
        (self.private / "config.toml").write_text('cli_auth_credentials_store="auto"')
        link = self.root / "selected"
        link.symlink_to(self.source)
        self.assertTrue(supports_file_auth(link))
        link.unlink()
        link.symlink_to(self.private)
        self.assertFalse(supports_file_auth(link))

    def test_changed_config_during_parse_is_not_cached_as_allowed(self):
        path = self.source / "config.toml"
        path.write_text('cli_auth_credentials_store="file"')
        parse = codex_auth.tomllib.loads

        def changed(text):
            value = parse(text)
            path.write_text('cli_auth_credentials_store="auto"')
            return value

        with patch.object(codex_auth.tomllib, "loads", side_effect=changed):
            self.assertFalse(supports_file_auth(self.source))
        self.assertFalse(supports_file_auth(self.source))

    def test_replaced_config_during_parse_is_not_used_for_current_account(self):
        path = self.source / "config.toml"
        path.write_text('cli_auth_credentials_store="file"')
        parse = codex_auth.tomllib.loads

        def replaced(text):
            value = parse(text)
            replacement = path.with_suffix('.new')
            replacement.write_text('cli_auth_credentials_store="auto"')
            replacement.replace(path)
            return value

        with patch.object(codex_auth.tomllib, "loads", side_effect=replaced):
            self.assertFalse(supports_file_auth(self.source))
        self.assertFalse(supports_file_auth(self.source))

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

    def test_dict_shape_401_triggers_auth_switch_same_as_string_unauthorized(self):
        # Codex emits {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}
        # when the model provider returns a real HTTP 401. This must be treated the same
        # as the legacy string form so that a mid-run account switch is not lost.
        dict_401 = {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}
        stream_401 = {"codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": 401}}}
        string_unauth = {"codexErrorInfo": "unauthorized"}
        non_auth_4xx = {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 403}}}
        non_auth_5xx = {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}}
        other = {"codexErrorInfo": "other", "message": "Please sign in"}

        from parallel_codex_runner_core.codex_auth import _is_auth_error
        self.assertTrue(_is_auth_error(dict_401))
        self.assertTrue(_is_auth_error(stream_401))
        self.assertTrue(_is_auth_error(string_unauth))
        self.assertFalse(_is_auth_error(non_auth_4xx))
        self.assertFalse(_is_auth_error(non_auth_5xx))
        self.assertFalse(_is_auth_error(other))

        # changed_auth_for_block must also fire for the dict shape
        state_dict = {"error": dict_401}
        self.assertTrue(changed_auth_for_block(state_dict, self.private))
        state_stream = {"error": stream_401}
        self.assertTrue(changed_auth_for_block(state_stream, self.private))
        # But not when there is no identity change
        state_dict["auth"] = {"identity": file_auth(self.source).identity}
        self.assertFalse(changed_auth_for_block(state_dict, self.private))


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
