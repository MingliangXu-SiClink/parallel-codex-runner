import argparse
import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from parallel_codex_runner_core import app, codex_app_server


class InstructionPreparationTests(unittest.TestCase):
    def test_native_preparation_preserves_native_config_without_a_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            args = argparse.Namespace(codex_bin="codex", model=None, fast=None,
                workspace_mode="isolated", subagents=False, subagents_limit=8)
            resolver = app._AgentDeveloperInstructionResolver()

            def prepare_home(_source, home, *_args, **_kwargs):
                home.mkdir(parents=True)

            with patch.dict(os.environ, {"PCR_CODEX_TRANSPORT": "app-server"}), \
                 patch.object(app, "prepare_agent_codex_home", side_effect=prepare_home), \
                 patch.object(app, "read_effective_codex_developer_instructions",
                    side_effect=AssertionError("Do not start a throwaway app-server")) as probe:
                for idx, role in ((1, "candidate"), (6, "synthesis")):
                    command, home = app._prepare_additional_agent(
                        args=args, idx=idx, run_root=root, workspace=workspace,
                        workspaces_root=root / "workspaces", meta_root=root / "meta",
                        help_text="Usage: codex exec [OPTIONS]\n -c, --config <KEY=VALUE>\n --json\n",
                        real_codex_home=root / "real-home", effective_effort=None,
                        resume_session_id=None, retry=False, progress_callback=None,
                        agent_role=role, developer_instructions="Stage guidance.",
                        instruction_resolver=resolver, initial=True,
                    )
                    self.assertFalse(any(value.startswith("developer_instructions=") for value in command))
                    additional = json.loads((home.parent / "developer_instructions.json").read_text())
                    self.assertIn("Stage guidance.", additional)
                    self.assertIn("PCR workspace isolation requirement:", additional)
                    self.assertIn(f"agent_{idx:03d}", additional)
                probe.assert_not_called()


class RequiredInstructionMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def assert_no_submission(self, metadata, *, role="candidate", read_error=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, meta = root / "workspace", root / "meta"
            home = meta / "codex_home"
            workspace.mkdir()
            home.mkdir(parents=True)
            instruction_path = meta / "developer_instructions.json"
            if metadata is not None:
                instruction_path.write_text(metadata, encoding="utf-8")
            original_read = Path.read_text

            def read(path, *args, **kwargs):
                if path == instruction_path and read_error is not None:
                    raise read_error
                return original_read(path, *args, **kwargs)

            with patch.dict(os.environ, {"PCR_CODEX_TRANSPORT": "app-server"}), \
                 patch.object(app, "resolve_codex_executable", return_value="codex"), \
                 patch.object(Path, "read_text", read), \
                 patch.object(app.asyncio, "create_subprocess_exec", AsyncMock(
                     side_effect=AssertionError("Missing preparation must not start a process"))) as spawn, \
                 patch.object(codex_app_server, "adapted_command", side_effect=AssertionError(
                     "Missing preparation must not adapt an execution command")) as adapt:
                result = await app.run_one_agent(
                    6 if role == "synthesis" else 1, workspace, meta, home,
                    "Do not submit this input.", ["codex", "exec", "-"], agent_role=role)
            self.assertEqual(result.status, "error")
            self.assertIn("No input was submitted", result.error)
            self.assertIsNone(result.returncode)
            self.assertIsNone(result.codex_thread_id)
            spawn.assert_not_awaited()
            adapt.assert_not_called()

    async def test_missing_metadata_fails_before_any_candidate_or_synthesis_process(self):
        for role in ("candidate", "synthesis"):
            with self.subTest(role=role):
                await self.assert_no_submission(None, role=role)

    async def test_malformed_or_empty_metadata_cannot_silently_drop_pcr_guidance(self):
        for metadata in ("{broken", "null", "[]", '""', '"  "'):
            with self.subTest(metadata=metadata):
                await self.assert_no_submission(metadata)

    async def test_unreadable_metadata_fails_before_submission(self):
        await self.assert_no_submission('"PCR guidance."', read_error=PermissionError("QA read failure"))


class LiveInstructionTests(unittest.IsolatedAsyncioTestCase):
    def process(self):
        process = MagicMock()
        process.pid = 12345
        process.stdin.drain = AsyncMock()
        process.wait = AsyncMock(return_value=0)
        process.returncode = None
        return process

    def requests(self, process):
        return [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]

    async def test_guidance_is_application_context_on_start_and_cold_resume(self):
        for resume in (False, True):
            with self.subTest(resume=resume), tempfile.TemporaryDirectory() as tmp:
                process = self.process()
                process.stdout.readuntil = AsyncMock(side_effect=[
                    b'{"id":1,"result":{"userAgent":"Codex Desktop/0.159.2 (test)"}}\n',
                    b'{"id":2,"result":{"thread":{"id":"session"}}}\n',
                    b'{"id":3,"result":{"turn":{"id":"turn"}}}\n',
                    b'{"method":"item/completed","params":{"threadId":"session","turnId":"turn","item":{"type":"agentMessage","id":"answer","text":"done","phase":"final_answer"}}}\n',
                    b'{"method":"turn/completed","params":{"threadId":"session","turn":{"id":"turn","status":"completed"}}}\n',
                ])
                output = Path(tmp) / "final.md"
                command = ["codex", "exec", *(["resume", "session"] if resume else []),
                    "-c", 'model_reasoning_effort="high"', "-m", "test-model",
                    "--sandbox", "workspace-write", "--ask-for-approval", "on-request",
                    "-o", str(output), "-"]
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec",
                        AsyncMock(return_value=process)) as spawn, \
                     patch.object(codex_app_server.asyncio, "timeout",
                        side_effect=AssertionError("No preparation deadline")), \
                     patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), \
                     contextlib.redirect_stdout(io.StringIO()) as events:
                    self.assertEqual(await codex_app_server.run(command, "original input",
                        additional_developer_instructions="PCR stage and isolation guidance."), 0)
                spawn.assert_awaited_once()
                requests = self.requests(process)
                self.assertEqual([value["method"] for value in requests], [
                    "initialize", "initialized",
                    "thread/resume" if resume else "thread/start", "turn/start"])
                settings = requests[2]["params"]
                self.assertNotIn("developerInstructions", settings)
                self.assertEqual(settings["model"], "test-model")
                self.assertEqual(settings["sandbox"], "workspace-write")
                self.assertEqual(settings["approvalPolicy"], "on-request")
                self.assertEqual(requests[-1]["params"]["input"][0]["text"], "original input")
                self.assertEqual(requests[-1]["params"]["additionalContext"], {
                    "parallel_codex_runner": {"kind": "application",
                        "value": "PCR stage and isolation guidance."}})
                self.assertIn('"phase": "resuming"' if resume else '"phase": "creating_thread"', events.getvalue())
                self.assertEqual(output.read_text(), "done")

    async def test_pending_initialization_or_resume_is_cancellable_without_submission(self):
        for pending_initialize in (True, False):
            with self.subTest(pending_initialize=pending_initialize):
                process = self.process()
                preparing = asyncio.Event()
                count = 0

                async def read():
                    nonlocal count
                    count += 1
                    if count == 1 and not pending_initialize:
                        return b'{"id":1,"result":{"userAgent":"codex_cli_rs/0.159.2"}}\n'
                    preparing.set()
                    await asyncio.Event().wait()

                process.stdout.readuntil = read
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec",
                        AsyncMock(return_value=process)) as spawn, \
                     patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), \
                     contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(codex_app_server.run(
                        ["codex", "exec", "resume", "session", "-o", "/tmp/unused-final.md", "-"], "original input",
                        additional_developer_instructions="PCR guidance."))
                    try:
                        await asyncio.wait_for(preparing.wait(), 1)
                        await asyncio.sleep(0.02)
                        self.assertFalse(task.done())
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                self.assertEqual([r["method"] for r in self.requests(process)],
                    ["initialize"] if pending_initialize else ["initialize", "initialized", "thread/resume"])
                spawn.assert_awaited_once()
                process.stdin.close.assert_called_once()

    async def test_startup_can_finish_after_the_old_fifteen_second_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = self.process()
            preparing, release = asyncio.Event(), asyncio.Event()
            messages = iter([
                {"id": 1, "result": {"userAgent": "codex_cli_rs/0.159.2"}},
                {"id": "approval", "method": "item/commandExecution/requestApproval", "params": {}},
                {"id": 2, "result": {"thread": {"id": "session"}}},
                {"id": 3, "result": {"turn": {"id": "turn"}}},
                {"method": "item/completed", "params": {"threadId": "session", "turnId": "turn",
                    "item": {"id": "answer", "type": "agentMessage", "text": "done", "phase": "final_answer"}}},
                {"method": "turn/completed", "params": {"threadId": "session",
                    "turn": {"id": "turn", "status": "completed"}}},
            ])

            async def read():
                message = next(messages)
                if message.get("id") == 2:
                    preparing.set()
                    await release.wait()
                return json.dumps(message).encode() + b"\n"

            process.stdout.readuntil = read
            output = Path(tmp) / "final.md"
            with patch.object(codex_app_server.asyncio, "create_subprocess_exec",
                    AsyncMock(return_value=process)) as spawn, contextlib.redirect_stdout(io.StringIO()):
                task = asyncio.create_task(codex_app_server.run(
                    ["codex", "exec", "-o", str(output), "-"], "original input",
                    additional_developer_instructions="PCR guidance."))
                try:
                    await asyncio.wait_for(preparing.wait(), 1)
                    await asyncio.sleep(15.1)
                    self.assertFalse(task.done())
                    self.assertNotIn("turn/start", [value.get("method") for value in self.requests(process)])
                    release.set()
                    self.assertEqual(await asyncio.wait_for(task, 1), 0)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            spawn.assert_awaited_once()
            requests = self.requests(process)
            self.assertEqual([value.get("method") for value in requests if "method" in value],
                ["initialize", "initialized", "thread/start", "turn/start"])
            rejected = next(value for value in requests if value.get("id") == "approval")
            self.assertEqual(rejected["error"]["code"], -32601)
            self.assertEqual(output.read_text(), "done")

    async def test_thread_start_or_resume_config_errors_never_fall_back(self):
        for resume in (False, True):
            with self.subTest(resume=resume):
                process = self.process()
                process.stdout.readuntil = AsyncMock(side_effect=[
                    b'{"id":1,"result":{"userAgent":"codex_cli_rs/0.159.2"}}\n',
                    b'{"id":2,"error":{"message":"invalid project config"}}\n',
                ])
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec",
                        AsyncMock(return_value=process)) as spawn, \
                     patch.dict(os.environ, {"PCR_CODEX_RESUME_LOCK": ""}), \
                     contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "invalid project config"):
                    await codex_app_server.run(["codex", "exec", *(["resume", "session"] if resume else []),
                        "-o", "/tmp/unused-final.md", "-"],
                        "original input", additional_developer_instructions="PCR guidance.")
                spawn.assert_awaited_once()
                self.assertNotIn("turn/start", [r["method"] for r in self.requests(process)])

    async def test_unrecognized_initialize_cannot_bypass_instruction_preservation(self):
        process = self.process()
        process.stdout.readuntil = AsyncMock(return_value=b'{"id":1,"error":{"message":"unsupported"}}\n')
        with patch.object(codex_app_server.asyncio, "create_subprocess_exec",
                AsyncMock(return_value=process)) as spawn, \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            await codex_app_server.run(["codex", "exec", "-o", "/tmp/unused-final.md", "-"],
                "original input", additional_developer_instructions="PCR guidance.")
        spawn.assert_awaited_once()

    async def test_unknown_or_old_server_cannot_silently_ignore_turn_guidance(self):
        for identity in ("codex", "codex_cli_rs/0.159.1", "Codex Desktop/0.158.0 (test)"):
            with self.subTest(identity=identity):
                process = self.process()
                process.stdout.readuntil = AsyncMock(return_value=json.dumps({
                    "id": 1, "result": {"userAgent": identity}}).encode() + b"\n")
                with patch.object(codex_app_server.asyncio, "create_subprocess_exec",
                        AsyncMock(return_value=process)) as spawn, \
                     contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "No input was submitted"):
                    await codex_app_server.run(["codex", "exec", "-o", "/tmp/unused-final.md", "-"],
                        "original input", additional_developer_instructions="PCR guidance.")
                spawn.assert_awaited_once()
                self.assertEqual([r["method"] for r in self.requests(process)], ["initialize"])

    def test_supported_release_versions(self):
        for identity in ("codex_cli_rs/0.159.2", "Codex Desktop/0.159.2 (macOS)",
                         "codex_cli_rs/0.160.0 (linux)", "codex_cli_rs/1.0.0"):
            codex_app_server.require_application_context_support(identity)


if __name__ == "__main__":
    unittest.main()
