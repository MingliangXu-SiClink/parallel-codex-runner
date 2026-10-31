import asyncio
import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from parallel_codex_runner_core import codex_app_server as adapter
from parallel_codex_runner_core import app


TRANSIENT = {"message": "stream disconnected before completion", "codexErrorInfo": {
    "responseStreamDisconnected": {"httpStatusCode": 503}}}


class NativePeer:
    def __init__(self, history, outcome, requests):
        self.history, self.outcome, self.requests = history, outcome, requests
        self.queue = asyncio.Queue()
        self.process = MagicMock()
        self.process.pid = 999999999
        self.process.returncode = None
        self.process.stdin.drain = AsyncMock()
        self.process.stdin.write.side_effect = self.write
        self.process.stdout.readline = self.queue.get
        self.process.wait = AsyncMock(return_value=0)

    def emit(self, value):
        self.queue.put_nowait(json.dumps(value).encode() + b"\n")

    def write(self, raw):
        value = json.loads(raw)
        self.requests.append(value)
        method, key = value["method"], value.get("id")
        if method == "initialized":
            return
        if method == "initialize":
            result = {"userAgent": "codex/0.159.2"}
        elif method in {"thread/start", "thread/resume"}:
            result = {"thread": {"id": "private-thread", "cwd": str(Path.cwd())}}
        elif method == "thread/turns/list":
            result = {"data": [self.history["turn"]] if self.history.get("turn") else []}
        elif method == "thread/items/list":
            result = {"data": [{"turnId": self.history["turn"]["id"], "item": item}
                               for item in self.history["items"]], "nextCursor": None}
        elif method == "turn/start":
            turn_id = f"turn-{self.history.get('starts', 0) + 1}"
            self.history["starts"] = self.history.get("starts", 0) + 1
            outcome = self.outcome()
            status = "completed" if outcome in {"success", "lost_completed"} else "interrupted" if outcome == "interrupted" else "failed"
            error = {"message": "Please sign in", "codexErrorInfo": "unauthorized"} if outcome == "auth" else TRANSIENT
            if outcome in {"routing", "compact_routing"}:
                prefix = "Error running remote compact task: " if outcome == "compact_routing" else ""
                error = {"message": prefix + "workspace routing discovery timed out", "codexErrorInfo": "other"}
            self.history["turn"] = {"id": turn_id, "status": status, "error": None if status in {"completed", "interrupted"} else error}
            self.history["items"] = [{"type": "userMessage", "id": "input", "clientId": value["params"]["clientUserMessageId"],
                                       "content": value["params"]["input"]}]
            if status == "completed":
                self.history["items"].append({"type": "agentMessage", "id": "answer", "phase": "final_answer", "text": "completed without restarting the job"})
            if outcome == "lost_absent":
                self.history.pop("turn")
                self.queue.put_nowait(b"")
                return
            if outcome.startswith("lost_"):
                self.queue.put_nowait(b"")
                return
            self.emit({"method": "turn/started", "params": {"threadId": "private-thread", "turn": {"id": turn_id}}})
            self.emit({"id": key, "result": {"turn": {"id": turn_id}}})
            if status == "completed":
                self.emit({"method": "item/completed", "params": {"threadId": "private-thread", "turnId": turn_id,
                          "item": self.history["items"][-1]}})
            self.emit({"method": "turn/completed", "params": {"threadId": "private-thread", "turn": self.history["turn"]}})
            return
        else:
            raise AssertionError(f"Unexpected native method: {method}")
        self.emit({"id": key, "result": result})


@unittest.skipUnless(os.name == "posix", "PCR persistent worker uses POSIX execution locks")
class AutomaticRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "final.md"
        self.command = ["codex", "exec", "-o", str(self.output), "-"]
        self.history, self.requests, self.peers = {}, [], []
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.root / "private-home"), "PCR_CODEX_RESUME_LOCK": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def execute(self, outcomes, wait=None):
        steps = iter(outcomes)

        async def spawn(*_args, **_kwargs):
            peer = NativePeer(self.history, lambda: next(steps), self.requests)
            self.peers.append(peer)
            return peer.process

        async def immediately(state, error, _emit):
            state.update(error=error, recoveries=int(state.state.get("recoveries") or 0) + 1)

        with patch.object(adapter.asyncio, "create_subprocess_exec", side_effect=spawn), \
             patch.object(adapter.AgentRecovery, "wait", wait or immediately), contextlib.redirect_stdout(io.StringIO()):
            return await adapter.run(self.command, "original expensive task", automatic_recovery=True)

    def starts(self):
        return [request for request in self.requests if request["method"] == "turn/start"]

    def state(self):
        return json.loads((self.root / "execution_recovery.json").read_text())

    async def test_more_than_native_retry_limit_recovers_in_original_thread_without_replaying_task(self):
        existing = self.root / "trained-result.bin"
        existing.write_bytes(b"existing trained result")
        self.assertEqual(await self.execute(["transient"] * 7 + ["success"]), 0)
        starts = self.starts()
        self.assertEqual(len(starts), 8)
        self.assertEqual(starts[0]["params"]["input"][0]["text"], "original expensive task")
        self.assertTrue(all(start["params"]["input"][0]["text"] == adapter.RECOVERY_INPUT for start in starts[1:]))
        self.assertTrue(all(start["params"]["threadId"] == "private-thread" for start in starts))
        self.assertEqual(len({start["params"]["clientUserMessageId"] for start in starts}), 8)
        self.assertNotIn("parallel_codex_runner_recovery", starts[0]["params"].get("additionalContext", {}))
        for start in starts[1:]:
            context = start["params"]["additionalContext"]["parallel_codex_runner_recovery"]
            self.assertEqual(context["kind"], "application")
            self.assertEqual(context["value"], adapter.RECOVERY_CONTEXT + "\n" + adapter.RECOVERY_ID_PREFIX
                + start["params"]["clientUserMessageId"])
        self.assertEqual(existing.read_bytes(), b"existing trained result")
        self.assertEqual(self.state()["stage"], "completed")
        self.assertEqual(self.state()["recoveries"], 7)
        self.assertEqual(self.state()["input_origin"], adapter.RECOVERY_ORIGIN)
        self.assertEqual(len(self.peers), 1)
        self.assertEqual(self.output.read_text(), "completed without restarting the job")

    async def test_lost_start_reply_is_reconciled_by_native_client_message_identity(self):
        self.assertEqual(await self.execute(["lost_failed", "success"]), 0)
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual(self.starts()[1]["params"]["input"][0]["text"], adapter.RECOVERY_INPUT)
        self.assertTrue(any(request["method"] == "thread/items/list" for request in self.requests))

    async def test_native_interruption_without_user_stop_recovers_the_unfinished_task(self):
        self.assertEqual(await self.execute(["interrupted", "success"]), 0)
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual(self.starts()[1]["params"]["input"][0]["text"], adapter.RECOVERY_INPUT)
        self.assertEqual(len(self.peers), 1)

    async def test_lost_success_reply_reads_original_final_answer_without_new_turn(self):
        self.assertEqual(await self.execute(["lost_completed", "unused"]), 0)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.output.read_text(), "completed without restarting the job")

    async def test_uncertain_submission_stays_owned_and_stop_prevents_future_recovery(self):
        checking = asyncio.Event()

        async def wait(state, error, emit):
            if isinstance(error.get("message"), str) and "durable native receipt" in error["message"]:
                checking.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(self.execute(["lost_absent", "unused"], wait))
        await asyncio.wait_for(checking.wait(), 1)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.state()["stage"], "submitting")
        (self.root / "execution_stop").write_text("cancelled")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.state()["stage"], "stopped")
        with patch.object(adapter.asyncio, "create_subprocess_exec", side_effect=AssertionError("stopped execution restarted")):
            self.assertEqual(await adapter.run(self.command, "original expensive task", automatic_recovery=True), 130)

    async def test_authentication_block_keeps_error_without_automatic_resubmission(self):
        self.assertEqual(await self.execute(["auth"]), 1)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.state()["error"]["codexErrorInfo"], "unauthorized")
        self.assertEqual(self.state()["stage"], "blocked")

    async def test_routing_timeout_after_native_retries_resumes_the_same_task(self):
        for outcome in ("routing", "compact_routing"):
            with self.subTest(outcome=outcome):
                self.history.clear()
                self.requests.clear()
                (self.root / "execution_recovery.json").unlink(missing_ok=True)
                self.assertEqual(await self.execute([outcome] * 7 + ["success"]), 0)
                self.assertEqual(len(self.starts()), 8)
                self.assertEqual(self.starts()[0]["params"]["input"][0]["text"], "original expensive task")
                self.assertTrue(all(value["params"]["input"][0]["text"] == adapter.RECOVERY_INPUT
                                    for value in self.starts()[1:]))
                self.assertEqual(self.state()["recoveries"], 7)

    async def test_old_misclassified_routing_block_reconciles_before_continuing(self):
        state = adapter.AgentRecovery(self.output, self.command, "original expensive task", None)
        state.acquire()
        error = {"message": "workspace routing discovery timed out", "codexErrorInfo": "other"}
        state.update(stage="blocked", thread_id="private-thread", turn_id="old-turn",
                     client_message_id="original-input", native_pid=999999999, error=error)
        state.close()
        self.history.update(turn={"id": "old-turn", "status": "failed", "error": error},
                            items=[{"id": "prior-tool", "type": "commandExecution", "status": "completed"}])
        self.assertEqual(await self.execute(["success"]), 0)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.starts()[0]["params"]["input"][0]["text"], adapter.RECOVERY_INPUT)
        self.assertNotIn("thread/start", [request["method"] for request in self.requests])

    async def test_reclassified_block_cannot_resume_without_its_original_receipt_or_with_a_live_owner(self):
        for change in ({"thread_id": ""}, {"turn_id": ""}, {"client_message_id": ""},
                       {"native_pid": os.getpid()}):
            with self.subTest(change=change):
                state = adapter.AgentRecovery(self.output, self.command, "original expensive task", None)
                state.acquire()
                state.update(stage="blocked", thread_id="private-thread", turn_id="old-turn",
                             client_message_id="original-input", native_pid=999999999,
                             error={"message": "workspace routing discovery timed out", "codexErrorInfo": "other"})
                state.update(**change)
                state.close()
                with self.assertRaises(adapter.AppServerError):
                    await self.execute(["success"])
                self.assertEqual(self.requests, [])

    async def test_durable_checkpoint_recovers_after_adapter_restart(self):
        state = adapter.AgentRecovery(self.output, self.command, "original expensive task", None)
        state.acquire()
        state.update(stage="accepted", thread_id="private-thread", turn_id="old-turn")
        state.close()
        self.history.update(turn={"id": "old-turn", "status": "failed", "error": TRANSIENT},
                            items=[{"id": "prior-tool", "type": "commandExecution", "status": "completed"}])
        self.assertEqual(await self.execute(["success"]), 0)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.starts()[0]["params"]["input"][0]["text"], adapter.RECOVERY_INPUT)
        self.assertNotIn("thread/start", [request["method"] for request in self.requests])

    async def test_identity_change_and_second_execution_owner_are_rejected_before_submission(self):
        state = adapter.AgentRecovery(self.output, self.command, "original expensive task", None)
        state.acquire()
        try:
            with self.assertRaises(BlockingIOError):
                await adapter.run(self.command, "original expensive task", automatic_recovery=True)
            state.update(stage="accepted", thread_id="private-thread", turn_id="old-turn")
        finally:
            state.close()
        with self.assertRaisesRegex(RuntimeError, "identity"):
            await adapter.run(self.command, "different task", automatic_recovery=True)
        self.assertEqual(self.requests, [])

    async def test_retry_is_cancelable_and_backoff_has_no_exhaustion_limit(self):
        state = adapter.AgentRecovery(self.output, self.command, "original expensive task", None)
        state.acquire()
        try:
            state.update(recoveries=10000)
            events = []
            with patch.object(adapter.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                await state.wait(TRANSIENT, events.append)
            self.assertLessEqual(sleep.await_args.args[0], adapter.RECOVERY_MAX_DELAY)
            self.assertEqual(state.state["recoveries"], 10001)
            self.assertEqual(events[0]["error"], TRANSIENT)
        finally:
            state.close()

    async def test_service_interruption_is_not_mistaken_for_a_user_stop(self):
        waiting = asyncio.Event()

        async def wait(_state, _error, _emit):
            waiting.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.execute(["transient"], wait))
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.state()["stage"], "continuation_ready")
        self.assertFalse((self.root / "execution_stop").exists())

    async def test_persistent_user_stop_cancels_backoff_even_without_a_parent_signal(self):
        waiting = asyncio.Event()
        stop_file = self.root / "control-stop"
        real_wait = adapter.AgentRecovery.wait

        async def wait(state, error, emit):
            waiting.set()
            await real_wait(state, error, emit)

        with patch.dict(os.environ, {"PCR_AGENT_STOP_FILES": json.dumps([str(stop_file)])}), \
             patch.object(adapter, "PROCESS_POLL_SECONDS", 0.01), contextlib.redirect_stdout(io.StringIO()):
            task = asyncio.create_task(self.execute(["transient"], wait))
            await asyncio.wait_for(waiting.wait(), 1)
            stop_file.write_text("user stop")
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.state()["stage"], "stopped")


class RecoveryErrorTests(unittest.TestCase):
    def test_native_retry_notification_keeps_metadata_without_a_terminal_failure(self):
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            recovery = adapter.AgentRecovery(Path(tmp) / "final.md", ["codex"], "prompt", None)
            observed = adapter.EventAdapter(events.append, recovery)
            observed.thread_id = "thread"
            observed.observe("error", {"threadId": "thread", "turnId": "turn", "willRetry": True, "error": TRANSIENT})
        self.assertEqual(events[0]["type"], "pcr.recovery")
        self.assertEqual(events[0]["error"], TRANSIENT)
        self.assertTrue(events[0]["will_retry"])

    def test_background_command_output_survives_recovery_without_accepting_foreign_items(self):
        events = []
        observed = adapter.EventAdapter(events.append)
        observed.thread_id, observed.turn_id = "thread", "recovery-turn"
        observed.completed_commands["job"] = {"id": "job", "type": "command_execution", "command": "train"}
        observed.command_turns["job"] = "previous-turn"
        observed.observe("item/commandExecution/outputDelta", {"threadId": "thread", "turnId": "previous-turn",
                         "itemId": "job", "delta": "progress"})
        observed.observe("item/commandExecution/outputDelta", {"threadId": "thread", "turnId": "foreign-turn",
                         "itemId": "unknown", "delta": "wrong"})
        observed.observe("item/commandExecution/outputDelta", {"threadId": "thread", "turnId": "foreign-turn",
                         "itemId": "job", "delta": "wrong"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["item"]["output_delta"], "progress")

    def test_retryable_codes_preserve_permanent_security_and_config_failures(self):
        self.assertTrue(adapter.transient_error(TRANSIENT))
        for kind in ("serverOverloaded", "rateLimitExceeded", "usageLimitExceeded", "internalServerError"):
            self.assertTrue(adapter.transient_error({"codexErrorInfo": kind}))
        for kind in ("unauthorized", "badRequest", "sandboxError", "cyberPolicy", "sessionBudgetExceeded"):
            self.assertFalse(adapter.transient_error({"codexErrorInfo": kind, "message": "high demand"}))
        for status in (400, 401, 403, 404):
            value = copy.deepcopy(TRANSIENT)
            value["codexErrorInfo"]["responseStreamDisconnected"]["httpStatusCode"] = status
            self.assertFalse(adapter.transient_error(value))
        value = copy.deepcopy(TRANSIENT)
        value["codexErrorInfo"]["responseStreamDisconnected"]["httpStatusCode"] = 200
        self.assertTrue(adapter.transient_error(value))


class RetainedExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace, self.home, self.meta = (self.root / name for name in ("workspace", "home", "meta"))
        for path in (self.workspace, self.home, self.meta):
            path.mkdir()
        self.command = ["codex", "exec", "-o", str(self.meta / "final_message.md"), "-"]
        self.prompt, self.instructions = "original task", "original permission boundaries"
        self.identity = hashlib.sha256(json.dumps([self.command, str(self.workspace.resolve()),
            str(self.home.resolve()), self.prompt, self.instructions], ensure_ascii=False).encode()).hexdigest()
        self.journal = {"version": 1, "identity": self.identity, "stage": "accepted", "native_pid": 999999999,
                        "thread_id": "original-thread", "turn_id": "original-turn"}
        (self.meta / "developer_instructions.json").write_text(json.dumps(self.instructions))

    def save(self):
        (self.meta / "execution_recovery.json").write_text(json.dumps(self.journal))

    def retained(self):
        return app.retained_agent_execution(self.meta, self.command, self.workspace, self.home,
                                            self.prompt, self.instructions)

    async def test_guardian_requires_identity_receipt_and_a_dead_native_owner(self):
        self.assertFalse(self.retained())
        self.save()
        with patch("parallel_codex_runner_core.plugin.lifecycle.pid_is_alive", return_value=False):
            self.assertTrue(self.retained())
            for values in ({"stage": "ready"}, {"stage": "blocked"}, {"stage": "stopped"},
                           {"identity": "different"}, {"native_pid": True}, {"native_pid": 0}, {"native_pid": None}):
                with self.subTest(values=values):
                    original = self.journal.copy()
                    self.journal.update(values)
                    self.save()
                    self.assertFalse(self.retained())
                    self.journal = original
            self.journal["stage"] = "completed"
            self.save()
            self.assertTrue(self.retained())
        with patch("parallel_codex_runner_core.plugin.lifecycle.pid_is_alive", return_value=True):
            self.assertFalse(self.retained())
        (self.meta / "execution_stop").touch()
        self.assertFalse(self.retained())

    async def execute(self, stop=None):
        processes = []

        async def spawn(*_args, **kwargs):
            self.assertEqual(kwargs["cwd"], str(self.workspace))
            self.assertEqual(kwargs["env"]["CODEX_HOME"], str(self.home.resolve()))
            index = len(processes)
            proc = MagicMock()
            proc.pid = 999999990 + index
            proc.returncode = -9 if index == 0 else 0
            proc.wait = AsyncMock(return_value=proc.returncode)
            proc.stdin.drain = AsyncMock()
            for stream in ("stdout", "stderr"):
                reader = asyncio.StreamReader()
                reader.feed_eof()
                setattr(proc, stream, reader)
            processes.append(proc)
            if index == 0:
                self.save()
                if stop is not None:
                    stop.set()
            else:
                (self.meta / "final_message.md").write_text("retained final answer")
            return proc

        with patch.object(app, "resolve_codex_executable", side_effect=lambda value: value), \
             patch.object(app.asyncio, "create_subprocess_exec", side_effect=spawn), \
             patch.object(adapter, "RECOVERY_MIN_DELAY", 0.01), \
             patch("parallel_codex_runner_core.plugin.lifecycle.pid_is_alive", return_value=False):
            result = await app.run_one_agent(1, self.workspace, self.meta, self.home, self.prompt, self.command,
                                             agent_cancel_event=stop)
        return result, processes

    async def test_dead_adapter_restarts_in_place_without_preparation_or_replaying_history(self):
        artifact = self.workspace / "trained-result.bin"
        artifact.write_bytes(b"existing result")
        result, processes = await self.execute()
        self.assertEqual(result.status, "success")
        self.assertEqual(len(processes), 2)
        self.assertEqual(artifact.read_bytes(), b"existing result")
        self.assertEqual(json.loads((self.meta / "execution_recovery.json").read_text())["turn_id"], "original-turn")
        self.assertTrue(all(proc.stdin.write.call_args.args == (self.prompt.encode(),) for proc in processes))

    async def test_explicit_stop_wins_over_adapter_restart(self):
        result, processes = await self.execute(asyncio.Event())
        self.assertIn(result.status, {"killed", "cancelled"})
        self.assertEqual(len(processes), 1)
        self.assertTrue((self.meta / "execution_stop").exists())
