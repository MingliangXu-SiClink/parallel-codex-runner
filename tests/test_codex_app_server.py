import copy
import asyncio
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from parallel_codex_runner_core.codex_app_server import EventAdapter, adapted_command, normalize_item, parse_exec_command
from parallel_codex_runner_core import codex_app_server
from parallel_codex_runner_core.work_operations import WorkOperationTracker
from parallel_codex_runner_core.remote.transcript import transcript_items
from parallel_codex_runner_core.plugin.events import _bounded_payloads
import json


class AppServerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_resume_loading_is_serial_but_execution_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            (sessions / "history.jsonl").write_text("large history")
            values = []

            async def resume(index):
                async with codex_app_server.resume_startup_slot("session"):
                    values.append((index, "start"))
                    await asyncio.sleep(0.06)
                    values.append((index, "ready"))
                await asyncio.sleep(0.1)
                values.append((index, "done"))

            with patch.dict(os.environ, {"CODEX_HOME": str(root), "PCR_CODEX_RESUME_LOCK": str(root / "startup.lock")}), \
                 patch.object(codex_app_server, "LARGE_RESUME_BYTES", 1):
                await asyncio.gather(resume(1), resume(2))
            self.assertEqual([phase for _, phase in values if phase != "done"], ["start", "ready", "start", "ready"])
            self.assertLess(values.index((2, "start")), values.index((1, "done")))

    async def test_cancelled_lock_wait_does_not_keep_the_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sessions").mkdir()
            (root / "sessions/history.jsonl").write_text("history")
            entered = asyncio.Event()

            async def blocked():
                async with codex_app_server.resume_startup_slot("session"):
                    entered.set()

            with patch.dict(os.environ, {"CODEX_HOME": str(root), "PCR_CODEX_RESUME_LOCK": str(root / "startup.lock")}), \
                 patch.object(codex_app_server, "LARGE_RESUME_BYTES", 1):
                async with codex_app_server.resume_startup_slot("session"):
                    task = asyncio.create_task(blocked())
                    await asyncio.sleep(0.02)
                    self.assertFalse(entered.is_set())
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                async with codex_app_server.resume_startup_slot("session"):
                    self.assertFalse(entered.is_set())

    def process(self):
        process = MagicMock()
        process.stdin.drain = AsyncMock()
        process.wait = AsyncMock(return_value=0)
        process.returncode = None
        process.pid = 12345
        return process

    def requests(self, process):
        return [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]

    async def test_slow_protocol_steps_finish_without_deadlines_or_duplicate_submission(self):
        for resume in (False, True):
            with self.subTest(resume=resume), tempfile.TemporaryDirectory() as tmp:
                process = self.process()
                messages = iter([
                    {"id": 1, "result": {"userAgent": "codex"}},
                    {"id": 2, "result": {"thread": {"id": "session"}}},
                    {"method": "turn/started", "params": {"threadId": "session", "turn": {"id": "turn"}}},
                    {"id": 3, "result": {"turn": {"id": "turn", "status": "inProgress"}}},
                    {"method": "item/completed", "params": {"threadId": "session", "turnId": "turn",
                        "item": {"type": "agentMessage", "id": "answer", "text": "done", "phase": "final_answer"}}},
                    {"method": "turn/completed", "params": {"threadId": "session",
                        "turn": {"id": "turn", "status": "completed"}}},
                ])

                async def read():
                    await asyncio.sleep(0.03)
                    return json.dumps(next(messages)).encode() + b"\n"

                process.stdout.readline = read
                output = Path(tmp) / "final.md"
                command = ["codex", "exec", *(["resume", "session"] if resume else []), "-o", str(output), "-"]
                events = io.StringIO()
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn, \
                     patch.object(codex_app_server.asyncio, "timeout", side_effect=AssertionError("implicit execution deadline")), \
                     patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), contextlib.redirect_stdout(events):
                    self.assertEqual(await codex_app_server.run(command, "question"), 0)
                self.assertEqual(output.read_text(), "done")
                spawn.assert_awaited_once()
                requests = self.requests(process)
                self.assertEqual([r["method"] for r in requests], [
                    "initialize", "initialized", "thread/resume" if resume else "thread/start", "turn/start"])
                self.assertEqual(requests[-1]["params"]["input"][0]["text"], "question")
                started = [json.loads(line) for line in events.getvalue().splitlines()
                           if json.loads(line)["type"] == "turn.started"]
                self.assertEqual(started, [{"type": "turn.started", "turn_id": "turn"}])
                phases = [json.loads(line) for line in events.getvalue().splitlines()
                          if json.loads(line)["type"] == "pcr.phase"]
                self.assertEqual(phases[0]["phase"], "initializing")
                self.assertEqual(phases[-1]["phase"], "submitting")
                self.assertTrue(all(item["native_pid"] == process.pid and item["emitted_at"] > 0 for item in phases))
                self.assertEqual([item["elapsed_seconds"] for item in phases],
                                 sorted(item["elapsed_seconds"] for item in phases))

    async def test_pending_resume_remains_cancellable_and_never_submits_the_question(self):
        process = self.process()
        restoring = asyncio.Event()
        calls = 0

        async def read():
            nonlocal calls
            calls += 1
            if calls == 1:
                return b'{"id":1,"result":{"userAgent":"codex"}}\n'
            restoring.set()
            await asyncio.Event().wait()

        process.stdout.readline = read
        with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn, \
             patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}):
            task = asyncio.create_task(codex_app_server.run(
                ["codex", "exec", "resume", "session", "-o", "/tmp/unused-final.md", "-"], "question"))
            await asyncio.wait_for(restoring.wait(), 1)
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertNotIn("turn/start", [r["method"] for r in self.requests(process)])
        spawn.assert_awaited_once()
        process.stdin.close.assert_called_once()

    async def test_pending_turn_acceptance_does_not_claim_started_or_resend(self):
        process = self.process()
        accepting = asyncio.Event()
        calls = 0

        async def read():
            nonlocal calls
            calls += 1
            if calls == 1:
                return b'{"id":1,"result":{"userAgent":"codex"}}\n'
            if calls == 2:
                return b'{"id":2,"result":{"thread":{"id":"session"}}}\n'
            accepting.set()
            await asyncio.Event().wait()

        process.stdout.readline = read
        events = io.StringIO()
        with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn, \
             patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), contextlib.redirect_stdout(events):
            task = asyncio.create_task(codex_app_server.run(
                ["codex", "exec", "resume", "session", "-o", "/tmp/unused-final.md", "-"], "question"))
            await asyncio.wait_for(accepting.wait(), 1)
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            self.assertNotIn('"type": "turn.started"', events.getvalue())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual([r["method"] for r in self.requests(process)].count("turn/start"), 1)
        spawn.assert_awaited_once()

    async def test_completed_turn_does_not_wait_forever_for_a_missing_start_response(self):
        for status, expected in (("completed", 0), ("failed", 1)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                process = self.process()
                process.stdout.readline = AsyncMock(side_effect=[
                    b'{"id":1,"result":{"userAgent":"codex"}}\n',
                    b'{"id":2,"result":{"thread":{"id":"session"}}}\n',
                    b'{"method":"turn/started","params":{"threadId":"session","turn":{"id":"turn"}}}\n',
                    b'{"method":"turn/completed","params":{"threadId":"session","turn":{"id":"old","status":"completed"}}}\n',
                    b'{"method":"item/completed","params":{"threadId":"session","turnId":"turn","item":{"type":"agentMessage","id":"answer","text":"done","phase":"final_answer"}}}\n',
                    json.dumps({"method": "turn/completed", "params": {"threadId": "session",
                        "turn": {"id": "turn", "status": status}}}).encode() + b"\n",
                ])
                output = Path(tmp) / "final.md"
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn, \
                     patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), contextlib.redirect_stdout(io.StringIO()):
                    result = await asyncio.wait_for(codex_app_server.run(
                        ["codex", "exec", "resume", "session", "-o", str(output), "-"], "question"), 1)
                self.assertEqual(result, expected)
                self.assertEqual(output.read_text(), "done")
                self.assertEqual([r["method"] for r in self.requests(process)].count("turn/start"), 1)
                spawn.assert_awaited_once()

    async def test_cancellation_after_resume_is_observed_before_submitting_a_turn(self):
        process = self.process()
        process.stdout.readline = AsyncMock(side_effect=[
            b'{"id":1,"result":{"userAgent":"codex"}}\n',
            b'{"id":2,"result":{"thread":{"id":"session"}}}\n',
        ])

        def emit(line, **_):
            if json.loads(line).get("type") == "thread.started":
                task.cancel()

        with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)), \
             patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), patch("builtins.print", side_effect=emit):
            task = asyncio.create_task(codex_app_server.run(
                ["codex", "exec", "resume", "session", "-o", "/tmp/unused-final.md", "-"], "question"))
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertNotIn("turn/start", [r["method"] for r in self.requests(process)])

    async def test_resume_error_or_disconnection_never_falls_back_or_resubmits(self):
        for message in (b'', b'{"id":2,"error":{"code":-1,"message":"resume failed"}}\n'):
            with self.subTest(message=message):
                process = self.process()
                process.stdout.readline = AsyncMock(side_effect=[
                    b'{"id":1,"result":{"userAgent":"codex"}}\n', message])
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn, \
                     patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}):
                    with self.assertRaises(RuntimeError):
                        await codex_app_server.run(
                            ["codex", "exec", "resume", "session", "-o", "/tmp/unused-final.md", "-"], "question")
                spawn.assert_awaited_once()
                self.assertNotIn("turn/start", [r["method"] for r in self.requests(process)])

    async def test_process_death_is_detected_even_if_descendant_holds_stdout_open(self):
        process = self.process()
        calls = 0

        async def read():
            nonlocal calls
            calls += 1
            if calls == 1:
                return b'{"id":1,"result":{"userAgent":"codex"}}\n'
            process.returncode = 7
            await asyncio.Event().wait()

        process.stdout.readline = read
        with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)) as spawn, \
             patch.object(codex_app_server, "PROCESS_POLL_SECONDS", 0.01), \
             patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}):
            with self.assertRaisesRegex(RuntimeError, "exited with code 7"):
                await asyncio.wait_for(codex_app_server.run(
                    ["codex", "exec", "resume", "session", "-o", "/tmp/unused-final.md", "-"], "question"), 1)
        spawn.assert_awaited_once()

    async def test_explicit_initialization_failure_can_use_legacy_before_any_thread_request(self):
        process = self.process()
        process.stdout.readline = AsyncMock(return_value=b'{"id":1,"error":{"code":-32601,"message":"unsupported"}}\n')
        legacy = self.process()
        legacy.returncode = 0
        legacy.communicate = AsyncMock()
        with patch.object(codex_app_server.asyncio, "create_subprocess_exec", AsyncMock(side_effect=[process, legacy])) as spawn:
            command = ["codex", "exec", "-o", "/tmp/unused-final.md", "-"]
            self.assertEqual(await codex_app_server.run(command, "question"), 0)
        self.assertEqual(spawn.await_count, 2)
        self.assertEqual([r["method"] for r in self.requests(process)], ["initialize"])
        legacy.communicate.assert_awaited_once_with(b"question")


class AppServerAdapterTests(unittest.TestCase):
    def test_stream_snapshots_replace_one_item_across_pages_and_large_completion(self):
        values = []
        for index, text in enumerate(["hel", "hello", "hello final"]):
            values.extend(transcript_items([{"type": "agent_line", "event_id": index,
                "pcr_text_item_id": "attempt:message", "text": json.dumps({
                    "type": "item.updated", "item": {"type": "agent_message", "id": "message", "text": text}})}]))
        self.assertEqual(len({value["id"] for value in values}), 1)
        self.assertEqual({value["id"]: value["text"] for value in values}, {"text-attempt:message": "hello final"})
        complete = {"type": "agent_line", "pcr_text_item_id": "attempt:message", "text": json.dumps({
            "type": "item.completed", "item": {"type": "agent_message", "id": "message", "text": "x" * 400_000}})}
        fragments = _bounded_payloads(complete)
        projected = transcript_items([{**fragment, "event_id": index + 10} for index, fragment in enumerate(fragments)])
        self.assertEqual(projected[0]["id"], values[0]["id"])
        self.assertEqual(len(set(item["id"] for item in projected)), len(fragments))
        self.assertEqual("".join(item["text"] for item in projected), "x" * 400_000)

    def test_detached_command_keeps_its_loaded_runtime(self):
        with patch.dict("os.environ", {"PCR_CODEX_TRANSPORT": "app-server"}):
            command = adapted_command(["codex", "exec", "--json", "-o", "/tmp/final.md", "-"])
        self.assertEqual(command[1], "-c")
        self.assertIn("class EventAdapter", command[2])
        compile(command[2], "pinned-adapter", "exec")
        self.assertEqual(command[3:], [*(["--pcr-auto-recover"] if os.name == "posix" else []),
                                      "codex", "exec", "--json", "-o", "/tmp/final.md", "-"])

    def test_add_delete_preserve_actual_content_as_diff(self):
        for kind, prefix in [("add", "+"), ("delete", "-")]:
            item = normalize_item({"type": "fileChange", "id": "f", "changes": [
                {"path": "file.txt", "kind": {"type": kind}, "diff": "actual old or new text\n"},
            ]})
            self.assertIn(prefix + "actual old or new text", item["changes"][0]["diff"])

    def test_usage_does_not_attribute_previous_turn_tokens(self):
        events = []
        adapter = EventAdapter(events.append)
        adapter.thread_id = "root"
        adapter.observe("turn/started", {"threadId": "root", "turn": {"id": "turn"}})
        for total in [110, 130]:
            adapter.observe("thread/tokenUsage/updated", {"threadId": "root", "turnId": "turn",
                "tokenUsage": {"total": {"reasoningOutputTokens": total}, "last": {"reasoningOutputTokens": 10}}})
        self.assertEqual([e["usage"]["reasoning_output_tokens"] for e in events if e["type"] == "turn.usage"], [10, 30])
        adapter.observe("thread/tokenUsage/updated", {"threadId": "root", "turnId": "old", "tokenUsage": {}})
        self.assertEqual(len(events), 3)

    def test_exec_options_preserve_model_config_permissions_and_resume(self):
        server, params, output, resume = parse_exec_command([
            "codex", "exec", "resume", "--json", "-c", 'developer_instructions="keep me"',
            "-c", 'model_reasoning_effort="high"', "-m", "test-model",
            "--dangerously-bypass-approvals-and-sandbox", "--output-last-message", "/tmp/final.md", "session", "-",
        ])
        self.assertIn('developer_instructions="keep me"', server)
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["sandbox"], "danger-full-access")
        self.assertEqual(params["model"], "test-model")
        self.assertEqual(params["runtimeWorkspaceRoots"], [str(Path.cwd())])
        self.assertEqual((output, resume), (Path("/tmp/final.md"), "session"))
        with self.assertRaises(ValueError):
            parse_exec_command(["codex", "exec", "--unknown", "-"])

    def test_file_change_keeps_per_operation_diff_and_counts(self):
        item = normalize_item({"type": "fileChange", "id": "patch-1", "status": "completed", "changes": [
            {"path": "test.py", "kind": {"type": "update", "move_path": None},
             "diff": "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new\n"},
        ]})
        self.assertIn("-old\n+new", item["changes"][0]["diff"])
        record = WorkOperationTracker().observe({"type": "item.completed", "item": item})
        self.assertEqual((record["targets"][0]["added"], record["targets"][0]["deleted"]), (1, 1))
        self.assertTrue(record["details_available"])

    def test_stream_isolation_summary_only_and_final_answer(self):
        events = []
        adapter = EventAdapter(lambda value: events.append(copy.deepcopy(value)))
        adapter.thread_id = "root"
        adapter.observe("item/completed", {"threadId": "child", "item": {"type": "agentMessage", "text": "wrong"}})
        adapter.observe("item/completed", {"threadId": "root", "item": {"type": "agentMessage", "text": "historical"}})
        self.assertEqual(events, [])
        adapter.started_turn({"id": "turn"})
        adapter.observe("item/started", {"threadId": "root", "item": {"type": "agentMessage", "id": "a", "text": "", "phase": "final_answer"}})
        adapter.observe("item/agentMessage/delta", {"threadId": "root", "itemId": "a", "delta": "hello"})
        adapter.observe("item/completed", {"threadId": "root", "item": {"type": "agentMessage", "id": "a", "text": "hello!", "phase": "final_answer"}})
        self.assertEqual(adapter.final_message, "hello!")
        self.assertEqual(events[2]["item"]["text"], "hello")
        self.assertEqual(len(events), 4)
        reasoning = normalize_item({"type": "reasoning", "id": "r", "summary": ["public"], "content": ["private"]})
        self.assertNotIn("content", reasoning)
        self.assertEqual(reasoning["text"], "public")
        adapter.started_turn({"id": "turn"})
        self.assertIsNone(adapter.observe("turn/completed", {"threadId": "root", "turn": {"id": "old", "status": "completed"}}))
        self.assertEqual(adapter.observe("turn/completed", {"threadId": "root", "turn": {"id": "turn", "status": "completed"}}), 0)

    def test_metadata_only_patch_does_not_offer_empty_details(self):
        record = WorkOperationTracker().observe({"type": "item.completed", "item": {
            "type": "file_change", "id": "legacy", "changes": [{"path": "file", "kind": "add"}],
        }})
        self.assertFalse(record["details_available"])
