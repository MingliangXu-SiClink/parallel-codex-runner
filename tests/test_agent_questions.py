import asyncio
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from parallel_codex_runner_core import codex_app_server as adapter
from parallel_codex_runner_core.agent_questions import QuestionError, QuestionOwner, QuestionStore, async_questions, async_reply
from parallel_codex_runner_core.plugin_runtime import ManagedRun, PluginRunManager
from parallel_codex_runner_core.remote.controller import RemoteController, RemoteControllerError
from parallel_codex_runner_core.remote.protocol import RemoteRequest, ProtocolError
from parallel_codex_runner_core.remote.relay_bridge import RelayBridge
from parallel_codex_runner_core.remote.journal import CommandJournal
from parallel_codex_runner_core.remote.controller_relay import ControllerRelayProxy, ControllerRelayError


def params(blocking=True):
    return {"threadId": "original-thread", "turnId": "original-turn", "itemId": "question-item",
            "isBlocking": blocking, "questions": [{"id": "choice", "header": "Direction",
            "question": "Which direction?", "options": [
                {"label": "A", "description": "First direction"},
                {"label": "B", "description": "Second direction"}]}]}


def answers(value="A"):
    return {"choice": {"answers": [value]}}


def managed_output(root, agent=1):
    directory = root / "meta" / f"agent_{agent:03d}"
    directory.mkdir(parents=True)
    (root / ".pcr-plugin-run.json").write_text(json.dumps({"version": 2, "run_id": "question-run",
        "workspace": str(Path.cwd()), "artifact_token": "question-test-artifact", "supports_agent_questions": True}))
    return directory / "final.md"


class QuestionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.owner = QuestionOwner(self.root)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.owner.close)
        self.key = self.owner.open(71, params())

    def test_client_restart_and_workspace_switch_preserve_original_identity(self):
        reread = QuestionStore(self.root).get(self.key)
        self.assertEqual(reread["thread_id"], "original-thread")
        self.assertEqual(reread["turn_id"], "original-turn")
        self.assertEqual(reread["connection_id"], self.owner.id)
        self.assertEqual(self.owner.store.pending_count(), 1)
        self.assertEqual(self.owner.store.blocking_count(), 1)

    def test_answer_is_durable_before_native_delivery_and_duplicate_clicks_deliver_once(self):
        for _ in range(3):
            result = QuestionStore(self.root).answer(self.key, self.owner.id, "answer-1", answers())
            self.assertEqual(result["delivery"], "queued")
        queued = self.owner.take_answers()
        self.assertEqual(queued, [(self.key, 71, {"answers": answers()})])
        self.assertEqual(self.owner.take_answers(), [])
        self.owner.sent(self.key)
        self.owner.resolve(71)
        self.assertEqual(self.owner.store.get(self.key)["state"], "resolved")
        self.assertEqual(self.owner.store.answer(self.key, self.owner.id, "another-client", answers())["answers"], answers())
        self.assertEqual(self.owner.take_answers(), [])

    def test_concurrent_identical_answers_have_one_effect(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: QuestionStore(self.root).answer(
                self.key, self.owner.id, f"answer-{i}", answers()), range(20)))
        self.assertTrue(all(r["answers"] == answers() for r in results))
        self.assertEqual(len(self.owner.take_answers()), 1)

    def test_conflicting_answer_is_never_allowed_to_overwrite_committed_choice(self):
        self.owner.store.answer(self.key, self.owner.id, "one", answers())
        with self.assertRaises(QuestionError):
            self.owner.store.answer(self.key, self.owner.id, "two", answers("B"))
        self.assertEqual(self.owner.store.get(self.key)["answers"], answers())

    def test_concurrent_conflicting_answers_commit_only_one_choice(self):
        def commit(index):
            choice = "A" if index % 2 else "B"
            try:
                record = self.owner.store.answer(self.key, self.owner.id, f"choice-{index}", answers(choice))
                return record["answers"]["choice"]["answers"][0]
            except QuestionError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(commit, range(20)))
        committed = {result for result in results if result is not None}
        self.assertEqual(len(committed), 1)
        self.assertEqual(len(self.owner.take_answers()), 1)
        self.assertEqual(self.owner.take_answers(), [])

    def test_native_cleanup_racing_with_answer_never_claims_native_delivery(self):
        for index in range(10):
            with self.subTest(index=index):
                native_id = f"cleanup-race-{index}"
                key = self.owner.open(native_id, params())
                with ThreadPoolExecutor(max_workers=2) as pool:
                    answer = pool.submit(self.owner.store.answer, key, self.owner.id, f"answer-{index}", answers())
                    cleanup = pool.submit(self.owner.resolve, native_id)
                    try:
                        answer.result()
                    except QuestionError:
                        pass
                    cleanup.result()
                record = self.owner.store.get(key)
                self.assertEqual(record["state"], "invalidated")
                self.assertNotIn(record["delivery"], {"sent", "sending"})
                self.assertEqual(self.owner.take_answers(), [])

    def test_wrong_agent_connection_or_question_cannot_receive_answer(self):
        other = QuestionOwner(self.root / "other")
        try:
            other_key = other.open(71, params())
            for store, key, connection in ((self.owner.store, self.key, other.id),
                                            (other.store, self.key, other.id),
                                            (self.owner.store, other_key, self.owner.id)):
                with self.assertRaises(QuestionError):
                    store.answer(key, connection, "answer", answers())
            self.assertEqual(other.take_answers(), [])
        finally:
            other.close()

    def test_native_request_cleanup_is_not_reported_as_answered(self):
        self.owner.resolve(71)
        record = self.owner.store.get(self.key)
        self.assertEqual(record["state"], "invalidated")
        self.assertIsNone(record["answers"])
        with self.assertRaises(QuestionError):
            self.owner.store.answer(self.key, self.owner.id, "late", answers())

    def test_turn_completion_and_stop_invalidate_unsent_answers(self):
        self.owner.store.answer(self.key, self.owner.id, "one", answers())
        self.owner.resolve(turn_id="original-turn")
        self.assertEqual(self.owner.take_answers(), [])
        self.assertEqual(self.owner.store.get(self.key)["answers"], answers())
        self.assertEqual(self.owner.store.get(self.key)["state"], "invalidated")

    def test_native_restart_does_not_replay_answer_to_reused_rpc_id(self):
        self.owner.store.answer(self.key, self.owner.id, "one", answers())
        self.owner.take_answers()
        self.owner.close()
        self._cleanups.pop()
        replacement = QuestionOwner(self.root)
        self.addCleanup(replacement.close)
        replacement_key = replacement.open(71, params())
        self.assertNotEqual(self.key, replacement_key)
        self.assertEqual(replacement.take_answers(), [])
        previous = replacement.store.get(self.key)
        self.assertEqual(previous["state"], "invalidated")
        self.assertEqual(previous["delivery"], "unknown")
        self.assertEqual(previous["answers"], answers())

    def test_single_native_owner_lease_is_enforced(self):
        with self.assertRaises(BlockingIOError):
            QuestionOwner(self.root)

    def test_request_replay_cannot_replace_text_or_committed_answer(self):
        self.owner.store.answer(self.key, self.owner.id, "one", answers())
        self.assertEqual(self.owner.open(71, params()), self.key)
        changed = params()
        changed["questions"][0]["question"] = "A different decision"
        with self.assertRaises(QuestionError):
            self.owner.open(71, changed)
        self.assertEqual(len(self.owner.take_answers()), 1)

    def test_grouped_questions_and_free_text_are_validated_without_guessing(self):
        body = params()
        body["questions"].append({"id": "comment", "header": "Comment", "question": "Any constraints?"})
        key = self.owner.open("two", body)
        with self.assertRaises(QuestionError):
            self.owner.store.answer(key, self.owner.id, "partial", answers())
        choice = {**answers(), "comment": {"answers": ["Keep existing files"]}}
        self.owner.store.answer(key, self.owner.id, "complete", choice)
        self.assertEqual(self.owner.take_answers()[0][2], {"answers": choice})

    def test_unknown_option_or_default_empty_answer_is_not_consent(self):
        for value in ("", "C", None, "x" * 8193):
            with self.subTest(value=str(value)[:20]), self.assertRaises(QuestionError):
                self.owner.store.answer(self.key, self.owner.id, "bad", answers(value))
        self.assertEqual(self.owner.take_answers(), [])

    def test_nonblocking_question_is_not_a_blocking_execution_state(self):
        self.owner.resolve(71)
        self.owner.open("async", params(False))
        self.assertEqual(self.owner.store.pending_count(), 1)
        self.assertEqual(self.owner.store.blocking_count(), 0)

    def test_empty_options_allow_explicit_free_text_without_a_default_answer(self):
        body = params()
        body["questions"][0]["options"] = []
        key = self.owner.open("free-text", body)
        self.assertIsNone(self.owner.store.get(key)["answers"])
        self.owner.store.answer(key, self.owner.id, "custom", answers("Keep the original files"))
        self.assertEqual(self.owner.take_answers()[0][2]["answers"], answers("Keep the original files"))

    def test_abrupt_adapter_death_releases_lease_and_retains_unconfirmed_choice(self):
        for after_write in (False, True):
            with self.subTest(after_write=after_write):
                directory = self.root / f"crash-owner-{after_write}"
                code = ("import json,os,sys; from pathlib import Path; "
                        "from parallel_codex_runner_core.agent_questions import QuestionOwner; "
                        f"o=QuestionOwner(Path({str(directory)!r})); "
                        f"key=o.open(71,{params()!r}); "
                        "print(json.dumps([key,o.id]),flush=True); sys.stdin.readline(); "
                        "pending=o.take_answers(); "
                        + ("print(json.dumps(pending[0][2]),flush=True); " if after_write else "")
                        + "os._exit(0)")
                with subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) as process:
                    key, connection = json.loads(process.stdout.readline())
                    store = QuestionStore(directory)
                    store.answer(key, connection, "saved-choice", answers())
                    response, _ = process.communicate("crash\n", timeout=5)
                    self.assertEqual(process.returncode, 0)
                    if after_write:
                        self.assertEqual(json.loads(response), {"answers": answers()})
                    else:
                        self.assertEqual(response, "")
                previous = store.get(key)
                self.assertEqual(previous["state"], "invalidated")
                self.assertEqual(previous["delivery"], "unknown")
                self.assertEqual(previous["answers"], answers())
                replacement = QuestionOwner(directory)
                try:
                    replacement.open(71, params())
                    self.assertEqual(replacement.take_answers(), [])
                finally:
                    replacement.close()

    def test_questions_survive_more_than_one_ui_page_without_deleting_history(self):
        for index in range(25):
            self.owner.open(f"request-{index}", params(False))
        first = self.owner.store.snapshot()
        self.assertEqual(len(first), 20)
        for request in first:
            self.owner.store.answer(request["id"], self.owner.id, request["id"], answers())
        second = self.owner.store.snapshot()
        self.assertTrue(any(r["id"] not in {v["id"] for v in first} for r in second))
        self.assertEqual(self.owner.store.get(self.key)["id"], self.key)

    def test_store_is_private_and_symlinks_are_rejected(self):
        self.assertEqual(self.owner.store.path.stat().st_mode & 0o777, 0o600)
        directory = self.root / "tampered"
        directory.mkdir()
        (directory / "agent_questions.sqlite3").symlink_to(self.owner.store.path)
        with self.assertRaises((QuestionError, OSError)):
            QuestionOwner(directory)

    def test_daemon_crash_before_or_after_commit_recovers_without_duplicate_native_reply(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                key = self.owner.open(f"crash-{after_commit}", params())
                journal = CommandJournal(self.root / f"commands-{after_commit}")
                request = RemoteRequest("runs.answer_question", {"question": key}, command_id="original-answer")
                def apply():
                    return self.owner.store.answer(key, self.owner.id, "original-answer", answers())
                def crash():
                    if after_commit:
                        apply()
                    raise KeyboardInterrupt("Disposable daemon crash")
                with self.assertRaises(KeyboardInterrupt):
                    journal.execute(request, crash)
                result, _ = CommandJournal(journal.directory).execute(request, apply)
                self.assertEqual(result["answers"], answers())
                self.assertEqual(len(self.owner.take_answers()), 1)
                self.assertEqual(self.owner.take_answers(), [])


class InteractivePeer:
    def __init__(self, *, blocking=True, permission=False):
        self.lines = asyncio.Queue()
        self.calls = []
        self.stdout = self
        self.stdin = self
        self.pid = os.getpid()
        self.returncode = None
        self.blocking = blocking
        self.permission = permission
        self.replied = asyncio.Event()

    def event(self, method, payload):
        self.lines.put_nowait({"method": method, "params": payload})

    def write(self, data):
        message = json.loads(data)
        self.calls.append(message)
        method = message.get("method")
        if method == "initialize":
            self.lines.put_nowait({"id": message["id"], "result": {"userAgent": "codex/0.159.2"}})
        elif method in {"thread/start", "thread/resume"}:
            self.lines.put_nowait({"id": message["id"], "result": {"thread": {
                "id": "original-thread", "cwd": str(Path.cwd())}}})
        elif method == "turn/start":
            self.lines.put_nowait({"id": message["id"], "result": {"turn": {"id": "original-turn", "status": "inProgress"}}})
            self.event("turn/started", {"threadId": "original-thread", "turn": {"id": "original-turn"}})
            self.lines.put_nowait({"id": 71,
                "method": "item/permissions/requestApproval" if self.permission else "item/tool/requestUserInput",
                "params": params(self.blocking)})
            self.event("item/completed", {"threadId": "original-thread", "turnId": "original-turn",
                "item": {"type": "agentMessage", "id": "partial", "text": "Independent work progressed", "phase": "commentary"}})
        elif message.get("id") == 71 and "result" in message:
            self.replied.set()
            self.event("serverRequest/resolved", {"threadId": "original-thread", "requestId": 71})
            self.event("item/completed", {"threadId": "original-thread", "turnId": "original-turn",
                "item": {"type": "agentMessage", "id": "final", "text": message["result"]["answers"]["choice"]["answers"][0], "phase": "final_answer"}})
            self.event("turn/completed", {"threadId": "original-thread", "turn": {"id": "original-turn", "status": "completed"}})

    async def readuntil(self):
        return json.dumps(await self.lines.get()).encode() + b"\n"

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.returncode = 0

    async def wait(self):
        self.returncode = 0
        return 0


async def question_ready(store):
    while not store.snapshot():
        await asyncio.sleep(0.005)
    return store.snapshot()[0]


class AsyncPeer(InteractivePeer):
    def __init__(self, *, lose_ack=False, reject=False):
        super().__init__()
        self.lose_ack = lose_ack
        self.reject = reject

    def write(self, data):
        message = json.loads(data)
        method = message.get("method")
        if method == "turn/start":
            self.calls.append(message)
            self.lines.put_nowait({"id": message["id"], "result": {"turn": {"id": "original-turn", "status": "inProgress"}}})
            self.event("turn/started", {"threadId": "original-thread", "turn": {"id": "original-turn"}})
            self.event("item/completed", {"threadId": "original-thread", "turnId": "original-turn",
                "item": async_item()})
        elif method == "turn/steer":
            self.calls.append(message)
            if self.reject:
                self.lines.put_nowait({"id": message["id"], "error": {"code": -32602, "message": "No active turn"}})
                return
            if not self.lose_ack:
                self.lines.put_nowait({"id": message["id"], "result": {"turnId": "original-turn"}})
            self.event("item/completed", {"threadId": "original-thread", "turnId": "original-turn",
                "item": {"type": "agentMessage", "id": "final", "text": "Original session continued", "phase": "final_answer"}})
            self.event("turn/completed", {"threadId": "original-thread", "turn": {"id": "original-turn", "status": "completed"}})
        else:
            super().write(data)


def async_item():
    return {"type": "agentMessage", "id": "call-original-async", "delivery": "async", "phase": "final_answer",
        "text": "Choose a direction", "questions": [{"title": "Choose a direction", "options": ["A", "B"]}]}


class AsyncQuestionTests(unittest.TestCase):
    def test_structured_async_questions_survive_without_a_blocking_rpc_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = QuestionStore(Path(tmp))
            params = async_questions("thread", "turn", async_item())
            key = store.open_async(params)
            record = store.get(key)
            self.assertTrue(record["is_blocking"] is False and record["state"] == "open")
            question_id = record["questions"][0]["id"]
            self.assertEqual(json.loads(question_id), ["request_user_input_async", "call-original-async", 0])
            answer = {question_id: {"answers": ["B"]}}
            store.answer(key, record["connection_id"], "original-answer", answer)
            store.answer(key, record["connection_id"], "original-answer", answer)
            self.assertEqual(len(store.queued_async()), 1)
            reply = async_reply(record, answer)
            self.assertIn('"questionItemId":', reply)
            self.assertIn('"answer":"B"', reply)
            self.assertEqual(QuestionStore(Path(tmp)).get(key)["answers"], answer)
            self.assertTrue(store.async_delivery(key, "queued", "sending"))
            self.assertFalse(store.async_delivery(key, "queued", "sending"))
            self.assertEqual(store.get(key)["delivery"], "unknown")

    def test_existing_owner_output_is_imported_once_without_parsing_plain_text_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "stdout.log"
            rows = [{"type": "thread.started", "thread_id": "thread"},
                {"type": "turn.started", "turn_id": "turn"},
                {"type": "item.completed", "item": async_item()}]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            store = QuestionStore(root)
            store.import_async_output(path)
            first = store.snapshot()
            store.import_async_output(path)
            self.assertEqual(store.snapshot(), first)
            with path.open("a") as output:
                output.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "id": "plain", "text": "A or B?"}}) + "\n")
            store.import_async_output(path)
            self.assertEqual(store.snapshot(), first)
            self.assertEqual(store.pending_count(), 1)
            self.assertEqual(store.blocking_count(), 0)

    def test_late_import_cannot_replace_newer_cursor_or_reopen_delivered_answer(self):
        for rotate, import_new in ((False, True), (True, True), (True, False)):
            with self.subTest(rotate=rotate, import_new=import_new), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "stdout.log"
                prefix = [{"type": "thread.started", "thread_id": "thread"},
                          {"type": "turn.started", "turn_id": "turn"}]
                path.write_text("\n".join(map(json.dumps, prefix)) + "\n")
                store = QuestionStore(root, async_only=True)
                store.import_async_output(path)
                original = {"type": "item.completed", "item": async_item()}
                with path.open("a") as output:
                    output.write(json.dumps(original) + "\n")
                entered, release = threading.Event(), threading.Event()

                def slow_parse(*args):
                    if not entered.is_set():
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("test reader was not released")
                    return async_questions(*args)

                with patch("parallel_codex_runner_core.agent_questions.async_questions", side_effect=slow_parse), \
                     ThreadPoolExecutor(max_workers=1) as pool:
                    late = pool.submit(store.import_async_output, path)
                    try:
                        self.assertTrue(entered.wait(2))
                        changed = async_item()
                        changed["id"] = "new-question"
                        next_row = {"type": "item.completed", "item": changed}
                        if rotate:
                            path.rename(root / "old-output.log")
                            path.write_text("\n".join(map(json.dumps, prefix + [next_row])) + "\n")
                        else:
                            with path.open("a") as output:
                                output.write(json.dumps(next_row) + "\n")
                        if import_new:
                            store.import_async_output(path)
                            record = store.snapshot()[0]
                            store.answer(record["id"], record["connection_id"], "original-answer",
                                         {record["questions"][0]["id"]: {"answers": ["B"]}})
                            self.assertTrue(store.async_delivery(record["id"], "queued", "sent"))
                        with store.connection() as db:
                            expected = tuple(db.execute("SELECT * FROM async_projection WHERE id=1").fetchone())
                        records = store.snapshot()
                    finally:
                        release.set()
                    late.result(timeout=2)
                self.assertEqual(store.snapshot(), records)
                with store.connection() as db:
                    self.assertEqual(tuple(db.execute("SELECT * FROM async_projection WHERE id=1").fetchone()), expected)
                store.import_async_output(path)
                self.assertEqual(len(store.snapshot()), 1 if rotate else 2)
                if import_new:
                    self.assertEqual(store.get(record["id"])["delivery"], "sent")
                    self.assertEqual(store.get(record["id"])["answer_id"], "original-answer")

    def test_projection_cursor_and_questions_commit_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "stdout.log"
            second = async_item()
            second["id"] = "second-question"
            rows = [{"type": "thread.started", "thread_id": "thread"},
                    {"type": "turn.started", "turn_id": "turn"},
                    {"type": "item.completed", "item": async_item()},
                    {"type": "item.completed", "item": second}]
            path.write_text("\n".join(map(json.dumps, rows)) + "\n")
            store = QuestionStore(root, async_only=True)
            insert = QuestionStore._open_async_record

            def fail_second(db, params):
                if params["itemId"] == "second-question":
                    raise OSError("injected interruption before cursor commit")
                return insert(db, params)

            with patch.object(QuestionStore, "_open_async_record", side_effect=fail_second), self.assertRaises(OSError):
                store.import_async_output(path)
            self.assertEqual(store.snapshot(), [])
            with store.connection() as db:
                self.assertIsNone(db.execute("SELECT * FROM async_projection WHERE id=1").fetchone())
            store.import_async_output(path)
            self.assertEqual(len(store.snapshot()), 2)
            with store.connection() as db:
                self.assertEqual(db.execute("SELECT cursor FROM async_projection WHERE id=1").fetchone()[0], path.stat().st_size)

    def test_reused_question_identity_does_not_block_later_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "stdout.log"
            store = QuestionStore(root)
            key = store.open_async(async_questions("thread", "turn", async_item()))
            changed = async_item()
            changed["questions"][0]["title"] = "Changed question"
            next_item = async_item()
            next_item["id"] = "next-question"
            rows = [{"type": "thread.started", "thread_id": "thread"},
                    {"type": "turn.started", "turn_id": "turn"},
                    {"type": "item.completed", "item": changed},
                    {"type": "item.completed", "item": next_item}]
            path.write_text("\n".join(map(json.dumps, rows)) + "\n")
            store.import_async_output(path)
            self.assertEqual(len(store.snapshot()), 2)
            self.assertEqual(store.get(key)["questions"][0]["question"], "Choose a direction")

    def test_idle_reply_reuses_workspace_home_agent_and_archives_the_original_receipt(self):
        from parallel_codex_runner_core.async_answers import run_idle_answer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = managed_output(root).with_name("final_message.md")
            meta = output.parent
            workspace, home = root / "original-workspace", meta / "codex_home"
            workspace.mkdir()
            home.mkdir()
            original = ["codex", "exec", "-m", "same-model", "--sandbox", "workspace-write",
                "--ask-for-approval", "never", "-o", str(output), "-"]
            previous = dict(command=original, workspace_dir=str(workspace), codex_home=str(home))
            (meta / "command.json").write_text(json.dumps(dict(command=original, cwd=str(workspace), codex_home=str(home))))
            receipt = dict(stage="completed", thread_id="thread", turn_id="turn", native_pid=99999999)
            (meta / "execution_recovery.json").write_text(json.dumps(receipt))
            output.write_text("Original successful output")
            (workspace / "retained.txt").write_text("Keep this result")
            store = QuestionStore(meta)
            key = store.open_async(async_questions("thread", "turn", async_item()))
            record = store.get(key)
            store.answer(key, record["connection_id"], "user-confirmed-answer", {record["questions"][0]["id"]: {"answers": ["B"]}})
            async def execute(*args, **kwargs):
                self.assertEqual(args[:4], (1, workspace, meta, home))
                self.assertIn("<send_user_message_question_reply>", args[4])
                self.assertEqual(args[5][-2:], ["thread", "-"])
                self.assertIn("same-model", args[5])
                kwargs["progress_callback"]({"type": "agent_line", "text": json.dumps({"type": "turn.started", "turn_id": "reply-turn"})})
                return "fixture-result"
            run = ManagedRun("fixture", "Original task, never repeated", str(workspace), {}, run_root=str(root))
            with patch("parallel_codex_runner_core.app.run_one_agent", side_effect=execute), \
                 patch("parallel_codex_runner_core.app.get_codex_home", return_value=root / "fixture-home"), \
                 patch("parallel_codex_runner_core.app.copy_codex_support_entries") as copy_support:
                self.assertEqual(run_idle_answer(run, 1, key, previous, lambda event: None, threading.Event(), threading.Event()), "fixture-result")
                copy_support.assert_called_once_with(root / "fixture-home", home)
            self.assertEqual(json.loads((meta / "answers" / key / "execution_recovery.json").read_text()), receipt)
            self.assertEqual((meta / "answers" / key / "final_message.md").read_text(), "Original successful output")
            self.assertEqual((workspace / "retained.txt").read_text(), "Keep this result")
            self.assertEqual(store.get(key)["delivery"], "sent")

    def test_workspace_lease_conflict_preserves_question_and_original_execution(self):
        from parallel_codex_runner_core.async_answers import run_idle_answer
        from parallel_codex_runner_core.plugin.lifecycle import RunOperationBusyError
        from parallel_codex_runner_core.workspace import source_workspace_lease
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = ManagedRun("fixture", "Original task", str(root), {}, run_root=str(root))
            with source_workspace_lease(root), patch("parallel_codex_runner_core.app.run_one_agent") as execute:
                with self.assertRaises(RunOperationBusyError):
                    run_idle_answer(run, 1, "question", {"workspace_dir": str(root)}, lambda event: None,
                        threading.Event(), threading.Event())
                execute.assert_not_called()
            self.assertFalse((root / "meta").exists())

    def test_stopped_idle_reply_never_submits_or_archives_original_execution(self):
        from parallel_codex_runner_core.async_answers import run_idle_answer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = managed_output(root)
            meta = output.parent
            store = QuestionStore(meta)
            key = store.open_async(async_questions("thread", "turn", async_item()))
            record = store.get(key)
            store.answer(key, record["connection_id"], "answer", {record["questions"][0]["id"]: {"answers": ["B"]}})
            (meta / "command.json").write_text("{}")
            (meta / "execution_recovery.json").write_text("{}")
            stop = threading.Event()
            stop.set()
            run = ManagedRun("fixture", "Original task", str(root), {}, run_root=str(root))
            with patch("parallel_codex_runner_core.app.run_one_agent") as execute, self.assertRaises(QuestionError):
                run_idle_answer(run, 1, key, {}, lambda event: None, stop, stop)
            execute.assert_not_called()
            self.assertEqual((meta / "execution_recovery.json").read_text(), "{}")

    def test_large_tool_output_partial_lines_and_log_rotation_do_not_hide_async_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "stdout.log"
            prefix = [{"type": "thread.started", "thread_id": "thread"}, {"type": "turn.started", "turn_id": "turn"}]
            path.write_text("\n".join(map(json.dumps, prefix)) + "\n" + json.dumps({"tool_output": "x" * (2 * 1024 * 1024)}) + "\n")
            store = QuestionStore(root)
            for _ in range(4):
                store.import_async_output(path)
            pending = json.dumps({"type": "item.completed", "item": async_item()})
            with path.open("a") as output:
                output.write(pending[:40])
            store.import_async_output(path)
            self.assertEqual(store.snapshot(), [])
            with path.open("a") as output:
                output.write(pending[40:] + "\n")
            store.import_async_output(path)
            self.assertEqual(len(store.snapshot()), 1)
            # An old blocking owner can invalidate its own RPC database, not
            # the separate, thread-addressed async question receipt.
            owner = QuestionOwner(root)
            owner.close()
            self.assertEqual(store.snapshot()[0]["state"], "open")
            path.rename(root / "old-output.log")
            changed = async_item()
            changed["id"] = "another-question"
            path.write_text("\n".join(map(json.dumps, prefix + [{"type": "item.completed", "item": changed}])) + "\n")
            store.import_async_output(path)
            self.assertEqual(len(store.snapshot()), 2)


class AsyncAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_steering_is_not_repeated_and_preserves_the_original_queued_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = managed_output(Path(tmp))
            store, peer = QuestionStore(output.parent), AsyncPeer(reject=True)
            with patch.object(adapter.asyncio, "create_subprocess_exec", AsyncMock(return_value=peer)), contextlib.redirect_stdout(io.StringIO()):
                task = asyncio.create_task(adapter.run(["codex", "exec", "-o", str(output), "-"], "Original task"))
                try:
                    record = await asyncio.wait_for(question_ready(store), 2)
                    store.answer(record["id"], record["connection_id"], "rejected-answer",
                        {record["questions"][0]["id"]: {"answers": ["B"]}})
                    await asyncio.sleep(1)
                    self.assertEqual(sum(call.get("method") == "turn/steer" for call in peer.calls), 1)
                    self.assertEqual(store.get(record["id"])["delivery"], "queued")
                    self.assertEqual(store.get(record["id"])["answer_id"], "rejected-answer")
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_async_answer_steers_the_original_turn_once_and_never_becomes_a_new_prompt(self):
        for lose_ack in (False, True):
            with self.subTest(lose_ack=lose_ack), tempfile.TemporaryDirectory() as tmp:
                output = managed_output(Path(tmp))
                store = QuestionStore(output.parent)
                peer = AsyncPeer(lose_ack=lose_ack)
                with patch.object(adapter.asyncio, "create_subprocess_exec", AsyncMock(return_value=peer)), contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(adapter.run(["codex", "exec", "-o", str(output), "-"], "Original task"))
                    record = await asyncio.wait_for(question_ready(store), 2)
                    answer = {record["questions"][0]["id"]: {"answers": ["B"]}}
                    store.answer(record["id"], record["connection_id"], "original-answer", answer)
                    await asyncio.wait_for(task, 3)
                steering = [call for call in peer.calls if call.get("method") == "turn/steer"]
                self.assertEqual(len(steering), 1)
                self.assertEqual(steering[0]["params"]["expectedTurnId"], "original-turn")
                self.assertEqual(steering[0]["params"]["threadId"], "original-thread")
                self.assertEqual(steering[0]["params"]["clientUserMessageId"], "pcr-answer-original-answer")
                self.assertEqual(sum(call.get("method") == "turn/start" for call in peer.calls), 1)
                self.assertEqual(store.get(record["id"])["delivery"], "unknown" if lose_ack else "sent")
                self.assertEqual(output.read_text(), "Original session continued")


class AdapterQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_agents_receive_different_answers_in_their_original_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            peers = [InteractivePeer(), InteractivePeer(blocking=False)]
            outputs = [managed_output(Path(tmp), agent) for agent in (1, 2)]
            stores = [QuestionStore(output.parent) for output in outputs]
            with patch.object(adapter.asyncio, "create_subprocess_exec", AsyncMock(side_effect=peers)), \
                 contextlib.redirect_stdout(io.StringIO()) as events:
                tasks = [asyncio.create_task(adapter.run(["codex", "exec", "-o", str(output), "-"], "Original task")) for output in outputs]
                try:
                    records = await asyncio.wait_for(asyncio.gather(*(question_ready(s) for s in stores)), 2)
                    self.assertTrue(all(not t.done() for t in tasks))
                    async def progressed():
                        while "Independent work progressed" not in events.getvalue():
                            await asyncio.sleep(0.005)
                    await asyncio.wait_for(progressed(), 2)
                    self.assertIn("Independent work progressed", events.getvalue())
                    stores[0].answer(records[0]["id"], records[0]["connection_id"], "answer-A", answers("A"))
                    await asyncio.wait_for(tasks[0], 2)
                    self.assertFalse(tasks[1].done())
                    stores[1].answer(records[1]["id"], records[1]["connection_id"], "answer-B", answers("B"))
                    await asyncio.wait_for(tasks[1], 2)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            self.assertEqual([output.read_text() for output in outputs], ["A", "B"])
            for peer in peers:
                self.assertEqual(sum(m.get("method") == "turn/start" for m in peer.calls), 1)
                self.assertEqual(sum(m.get("id") == 71 and "result" in m for m in peer.calls), 1)
                thread = next(m for m in peer.calls if m.get("method") == "thread/start")
                self.assertTrue(thread["params"]["config"]["features.default_mode_request_user_input"])

    async def test_stop_while_awaiting_answer_never_grants_permission_or_replies_later(self):
        for permission in (False, True):
            with self.subTest(permission=permission), tempfile.TemporaryDirectory() as tmp:
                peer = InteractivePeer(permission=permission)
                output = managed_output(Path(tmp))
                with patch.object(adapter.asyncio, "create_subprocess_exec", AsyncMock(return_value=peer)), \
                     contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(adapter.run(["codex", "exec", "-o", str(output), "-"], "task"))
                    if permission:
                        while not any(m.get("id") == 71 and "error" in m for m in peer.calls):
                            await asyncio.sleep(0.005)
                    else:
                        await asyncio.wait_for(question_ready(QuestionStore(output.parent)), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertFalse(peer.replied.is_set())
                self.assertFalse(any(m.get("id") == 71 and "result" in m for m in peer.calls))
                if not permission:
                    self.assertEqual(QuestionStore(output.parent).snapshot()[0]["state"], "invalidated")

    async def test_standalone_cli_does_not_enable_an_unanswerable_interaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            peer = InteractivePeer()
            with patch.object(adapter.asyncio, "create_subprocess_exec", AsyncMock(return_value=peer)), \
                 contextlib.redirect_stdout(io.StringIO()):
                task = asyncio.create_task(adapter.run(["codex", "exec", "-o", str(Path(tmp) / "final.md"), "-"], "task"))
                async def refused():
                    while not any(m.get("id") == 71 and "error" in m for m in peer.calls):
                        await asyncio.sleep(0.005)
                try:
                    await asyncio.wait_for(refused(), 2)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            thread = next(m for m in peer.calls if m.get("method") == "thread/start")
            self.assertNotIn("features.default_mode_request_user_input", thread["params"].get("config", {}))
            self.assertFalse(QuestionStore(Path(tmp)).path.exists())


class QuestionControllerTests(unittest.TestCase):
    def test_completed_agent_answer_can_queue_without_restarting_other_running_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            run_root = root / "runs/20261016_120000"
            run_root.mkdir(parents=True)
            manager = PluginRunManager(root / "state", detached_workers=False, run_maintenance=False)
            run = ManagedRun("question-run", "Original task", str(workspace), {"run_base": str(run_root.parent)},
                status="running", run_root=str(run_root), artifact_token="question-artifact",
                agent_statuses={1: "success", 2: "running"}, worker_pid=os.getpid())
            manager._runs[run.run_id] = run
            manager._artifacts.write_marker(run)
            store = manager._question_store(run, 1)
            key = store.open_async(async_questions("thread", "turn", async_item()))
            record = store.get(key)
            with patch.object(manager, "_schedule_async_answers_locked") as schedule:
                accepted = manager.answer_question(run.run_id, 1, key, record["connection_id"], "answer",
                    {record["questions"][0]["id"]: {"answers": ["B"]}})
                schedule.assert_not_called()
            self.assertEqual(accepted["request"]["delivery"], "queued")
            self.assertEqual(run.agent_statuses, {1: "success", 2: "running"})
            self.assertEqual(run.worker_pid, os.getpid())
            self.assertIsNone(run.finalized_agent)

    def test_async_questions_remain_read_only_after_stop_adoption_or_original_agent_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            run_root = root / "runs/20261016_120000"
            run_root.mkdir(parents=True)
            manager = PluginRunManager(root / "state", detached_workers=False, run_maintenance=False)
            run = ManagedRun("question-run", "Original task", str(workspace), {"run_base": str(run_root.parent)},
                status="completed", run_root=str(run_root), artifact_token="question-artifact", agent_statuses={1: "success"})
            manager._runs[run.run_id] = run
            manager._artifacts.write_marker(run)
            store = manager._question_store(run, 1)
            key = store.open_async(async_questions("thread", "turn", async_item()))
            record = store.get(key)
            manager._runs["unrelated"] = ManagedRun("unrelated", "Independent task", str(workspace), {})
            self.assertEqual(manager.get_question(run.run_id, 1, key)["request"]["state"], "open")
            for reason in ("stop", "adopted", "continuation"):
                with self.subTest(reason=reason):
                    run.finalized_agent = 1 if reason == "adopted" else None
                    if reason == "stop":
                        path = manager.control_dir / run.run_id / "stop"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.touch()
                    if reason == "continuation":
                        manager._runs["unrelated"].continued_from_run_id = run.run_id
                        manager._runs["unrelated"].continued_from_agent = 1
                    self.assertEqual(manager.get_question(run.run_id, 1, key)["request"]["state"], "invalidated")
                    self.assertEqual(manager._agent_summary_locked(run, 1)["pending_question_count"], 0)
                    with self.assertRaises(QuestionError):
                        manager.answer_question(run.run_id, 1, key, record["connection_id"], "answer",
                            {record["questions"][0]["id"]: {"answers": ["B"]}})
                    if reason == "stop":
                        path.unlink()
            self.assertEqual(store.get(key)["delivery"], "none")

    def test_capabilities_do_not_change_original_message_or_command_identity(self):
        request = RemoteRequest("turns.start", {"prompt": "original"}, client_id="original-client", command_id="original-command")
        upgraded = RemoteRequest.from_mapping({**request.to_mapping(), "client_capabilities": ["agent_questions"]})
        self.assertEqual(CommandJournal.identity(request), CommandJournal.identity(upgraded))
        self.assertEqual(upgraded.body, request.body)
        self.assertEqual(RemoteRequest.from_mapping(upgraded.to_mapping()).client_capabilities, ("agent_questions",))
        for value in ("agent_questions", [True], ["bad name"], ["x"] * 17):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                RemoteRequest.from_mapping({**request.to_mapping(), "client_capabilities": value})

    def test_registered_legacy_client_run_does_not_expose_unanswerable_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = managed_output(root)
            marker = root / ".pcr-plugin-run.json"
            data = json.loads(marker.read_text())
            for flag in (None, False, "true", True):
                with self.subTest(flag=flag):
                    marker.write_text(json.dumps({**data, "supports_agent_questions": flag}))
                    self.assertEqual(adapter.has_question_channel(output), flag is True)

    def test_only_question_capable_clients_enable_new_runs_and_explicit_continuations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = PluginRunManager(root / "state", detached_workers=False, run_maintenance=False)
            controller = RemoteController(manager=manager, state_dir=root / "remote")
            with patch.object(controller.conversations, "start_turn", return_value={"status": "started"}) as start:
                for capabilities in ((), ("agent_questions",)):
                    controller.dispatch(RemoteRequest("turns.start", {"workspace": str(workspace), "prompt": "task"},
                        client_capabilities=capabilities))
                    self.assertEqual(start.call_args.kwargs.get("supports_agent_questions", False), bool(capabilities))
            with patch.object(controller, "_ensure_continuation_context"), \
                 patch.object(controller.conversations, "continue_turn", return_value={}) as continuation:
                for capabilities in ((), ("agent_questions",)):
                    controller._runs_continue(RemoteRequest("turns.continue", {"run_id": "parent", "agent": 1, "prompt": "next"},
                        client_capabilities=capabilities))
                    self.assertEqual(continuation.call_args.kwargs["config_overrides"]["supports_agent_questions"], bool(capabilities))

    def test_relay_forwarding_preserves_client_capabilities_without_changing_prompt_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = PluginRunManager(root / "state", detached_workers=False, run_maintenance=False)
            controller = RemoteController(manager=manager, state_dir=root / "remote")
            with patch.object(controller.controller_relay, "call", return_value={"ok": True, "body": {}}) as call:
                controller.dispatch(RemoteRequest("controller.call", {"machine_id": "target", "machine_public_key": "pin",
                    "target_action": "turns.start", "target_body": {"prompt": "original"},
                    "target_command_id": "original-command"}, client_capabilities=("agent_questions",)))
                self.assertEqual(call.call_args.kwargs["client_capabilities"], ("agent_questions",))
                self.assertEqual(call.call_args.kwargs["body"], {"prompt": "original"})

    def test_relay_answer_reconciles_before_retrying_a_lost_mutation(self):
        for action, attempts in (("runs.answer_question", 1), ("runs.question", 2)):
            with self.subTest(action=action):
                proxy = ControllerRelayProxy.__new__(ControllerRelayProxy)
                proxy._connection_lock = threading.RLock()
                proxy._call_once = MagicMock(side_effect=OSError("dropped receipt"))
                proxy._close_websocket = MagicMock()
                with self.assertRaises(ControllerRelayError):
                    proxy.call(machine_id="target", machine_public_key="pin", action=action,
                        body={}, expected_revision=None, command_id="original-answer", timeout_seconds=1,
                        client_capabilities=("agent_questions",))
                self.assertEqual(proxy._call_once.call_count, attempts)
                self.assertEqual(proxy._call_once.call_args.kwargs["client_capabilities"], ("agent_questions",))

    def test_question_read_and_answer_are_scoped_and_do_not_mutate_run_or_adopt_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            run_root = root / "runs/20261016_120000"
            run_root.mkdir(parents=True)
            manager = PluginRunManager(root / "state", detached_workers=False, run_maintenance=False)
            run = ManagedRun("question-run", "original task", str(workspace), {"run_base": str(run_root.parent)},
                             status="running", run_root=str(run_root), artifact_token="question-artifact",
                             agent_statuses={1: "running", 2: "running"})
            manager._runs[run.run_id] = run
            manager._artifacts.write_marker(run)
            owner = QuestionOwner(run_root / "meta/agent_001")
            try:
                key = owner.open(71, params())
                controller = RemoteController(manager=manager, state_dir=root / "remote")
                read = controller.dispatch(RemoteRequest("runs.question", {"run_id": run.run_id, "agent": 1, "request_id": key}))
                self.assertEqual(read["request"]["id"], key)
                body = {"run_id": run.run_id, "agent": 1, "request_id": key,
                        "connection_id": owner.id, "answer_id": "answer-one", "answers": answers()}
                controller.dispatch(RemoteRequest("runs.answer_question", body))
                self.assertEqual(run.status, "running")
                self.assertIsNone(run.finalized_agent)
                self.assertEqual(run.agent_statuses, {1: "running", 2: "running"})
                with self.assertRaises(RemoteControllerError):
                    controller.dispatch(RemoteRequest("runs.answer_question", {**body, "agent": 2}))
                self.assertIn("runs.answer_question", controller.MUTATING_ACTIONS)
                self.assertIn("runs.question", controller.READ_ONLY_ACTIONS)
                self.assertIn("runs.answer_question", RelayBridge.REMOTE_ACTIONS)
                self.assertIn("runs.question", RelayBridge.REMOTE_ACTIONS)
                run.status = "finalizing"
                fresh = owner.open(72, params())
                with self.assertRaises(RemoteControllerError):
                    controller.dispatch(RemoteRequest("runs.answer_question", {**body, "request_id": fresh}))
            finally:
                owner.close()
