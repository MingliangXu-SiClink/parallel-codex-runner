import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

import parallel_codex_runner_core.tui_textual as tui_textual
import parallel_codex_runner_core.app as app_core
import parallel_codex_runner_core.workspace as workspace_core
from parallel_codex_runner import (
    AgentResult,
    AgentState,
    build_codex_command,
    cleanup_workspace_copy,
    cleanup_workspace_copies,
    copy_workspace,
    create_unique_run_root,
    extract_codex_thread_id_from_json,
    import_codex_session_to_workspace,
    load_codex_session_history,
    load_resume_sessions_from_state,
    parse_args,
    prepare_agent_codex_home,
    promote_codex_session_to_workspace,
    run_one_agent,
    scrub_codex_home_support_entries,
    stream_to_log,
    sync_back_with_python,
)
from parallel_codex_runner_core.tui_textual import command_suggestions, display_line_from_output, display_line_parts_from_output


class SyncBackTests(unittest.TestCase):
    def test_python_sync_deletes_destination_file_missing_from_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            dst = root / "dst"
            src.mkdir()
            dst.mkdir()

            (dst / "deleted.txt").write_text("old", encoding="utf-8")

            sync_back_with_python(src, dst)

            self.assertFalse((dst / "deleted.txt").exists())

    def test_python_sync_preserves_git_and_replaces_file_with_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            dst = root / "dst"
            src.mkdir()
            dst.mkdir()

            (src / "replace_me").mkdir()
            (src / "replace_me" / "new.txt").write_text("new", encoding="utf-8")
            (dst / "replace_me").write_text("old file", encoding="utf-8")

            (src / ".git").mkdir()
            (src / ".git" / "config").write_text("candidate git", encoding="utf-8")
            (dst / ".git").mkdir()
            (dst / ".git" / "config").write_text("original git", encoding="utf-8")

            sync_back_with_python(src, dst)

            self.assertTrue((dst / "replace_me").is_dir())
            self.assertEqual((dst / "replace_me" / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual((dst / ".git" / "config").read_text(encoding="utf-8"), "original git")


class WorkspaceCopyTests(unittest.TestCase):
    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_workspace_copy_uses_worktree_and_preserves_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(
                ["git", "-c", "init.defaultBranch=main", "init"],
                cwd=workspace,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=workspace, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)

            (workspace / "keep.txt").write_text("clean", encoding="utf-8")
            (workspace / "delete.txt").write_text("delete me", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, stdout=subprocess.PIPE)

            (workspace / "keep.txt").write_text("dirty", encoding="utf-8")
            (workspace / "delete.txt").unlink()
            (workspace / "untracked.txt").write_text("untracked", encoding="utf-8")

            run_base = root / "runs"
            dst = run_base / "workspaces" / "agent_001"
            copy_workspace(workspace, dst, run_base)
            try:
                self.assertTrue((dst / ".git").is_file())
                self.assertEqual((dst / "keep.txt").read_text(encoding="utf-8"), "dirty")
                self.assertFalse((dst / "delete.txt").exists())
                self.assertEqual((dst / "untracked.txt").read_text(encoding="utf-8"), "untracked")
            finally:
                cleanup_workspace_copy(workspace, dst)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_cleanup_workspace_copies_prunes_worktree_record_when_root_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = root / "workspace"
            workspaces_root = root / "runs" / "workspaces"
            dst = workspaces_root / "agent_001"
            workspace.mkdir()
            workspaces_root.mkdir(parents=True)
            subprocess.run(
                ["git", "-c", "init.defaultBranch=main", "init"],
                cwd=workspace,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=workspace, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
            (workspace / "keep.txt").write_text("clean", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, stdout=subprocess.PIPE)
            subprocess.run(
                ["git", "-C", str(workspace), "worktree", "add", "--detach", str(dst), "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            shutil.rmtree(workspaces_root)
            cleanup_workspace_copies(workspace, workspaces_root)

            result = subprocess.run(
                ["git", "-C", str(workspace), "worktree", "list", "--porcelain"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            self.assertNotIn(str(dst), result.stdout)

    def test_plain_workspace_copy_excludes_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            dst = root / "runs" / "workspaces" / "agent_001"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            (workspace / ".git" / "config").write_text("private git data", encoding="utf-8")
            (workspace / "file.txt").write_text("content", encoding="utf-8")

            with mock.patch.object(workspace_core, "copy_workspace_with_git_worktree", return_value=False):
                copy_workspace(workspace, dst, root / "runs")

            self.assertEqual((dst / "file.txt").read_text(encoding="utf-8"), "content")
            self.assertFalse((dst / ".git").exists())

    def test_cleanup_workspace_copy_raises_when_delete_leaves_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace_copy = root / "agent_001"
            workspace.mkdir()
            workspace_copy.mkdir()
            (workspace_copy / "file.txt").write_text("content", encoding="utf-8")

            with mock.patch.object(workspace_core.shutil, "rmtree", return_value=None):
                with self.assertRaises(OSError):
                    cleanup_workspace_copy(workspace, workspace_copy)


class RunRootTests(unittest.TestCase):
    def test_create_unique_run_root_adds_suffix_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_base = Path(tmp)
            (run_base / "20260706_010203").mkdir()

            run_root = create_unique_run_root(run_base, timestamp="20260706_010203")

            self.assertEqual(run_root.name, "20260706_010203_001")
            self.assertTrue(run_root.is_dir())


class CommandBuildTests(unittest.TestCase):
    def test_model_uses_short_flag_when_only_short_flag_is_supported(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  -m MODEL\n  --json\n",
            Path("final.md"),
            model="gpt-5",
        )

        self.assertTrue(caps["model"])
        self.assertIn("-m", cmd)
        self.assertNotIn("--model", cmd)

    def test_resume_command_uses_exec_resume_session_id_and_stdin_prompt(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]\n  --json\n  -m, --model <MODEL>\n  -o, --output-last-message <FILE>\n",
            Path("final.md"),
            model="gpt-5",
            resume_session_id="019f-test-session",
        )

        self.assertTrue(caps["resume"])
        self.assertEqual(cmd[:3], ["codex", "exec", "resume"])
        self.assertIn("--json", cmd)
        self.assertIn("--output-last-message", cmd)
        self.assertEqual(cmd[-2:], ["019f-test-session", "-"])

    def test_long_flag_detection_does_not_match_substrings(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  --json\n  --model-provider <PROVIDER>\n",
            Path("final.md"),
        )

        self.assertFalse(caps["model"])
        self.assertNotIn("--model", cmd)


class ArgParseTests(unittest.TestCase):
    def test_default_num_agents_is_five(self) -> None:
        args = parse_args(["fix tests"])

        self.assertEqual(args.num_agents, 5)


class RunOnceCleanupTests(unittest.TestCase):
    def test_run_once_cleans_partial_workspace_when_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runs = root / "runs"
            workspace.mkdir()
            (workspace / "file.txt").write_text("content", encoding="utf-8")
            args = parse_args(["prompt", "--workspace", str(workspace), "--runs-dir", str(runs), "-n", "1"])

            def fail_copy(_workspace: Path, dst: Path, run_base: Path) -> None:
                dst.mkdir(parents=True)
                (dst / "partial.txt").write_text("partial", encoding="utf-8")
                raise RuntimeError("copy failed")

            with mock.patch.object(app_core, "read_codex_exec_help", return_value="Usage: codex exec [OPTIONS] [PROMPT]\n  --json\n"):
                with mock.patch.object(app_core, "copy_workspace", side_effect=fail_copy):
                    with self.assertRaises(RuntimeError):
                        app_core.run_once(args, "prompt", progress_callback=lambda _payload: None, print_output=False)

            self.assertFalse(any(path.exists() for path in runs.glob("*/workspaces")))


class TuiCommandTests(unittest.TestCase):
    def test_tui_model_choices_use_visible_codex_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {"slug": "gpt-visible", "visibility": "list"},
                            {"slug": "gpt-hidden", "visibility": "hide"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(tui_textual, "get_codex_home", return_value=root):
                options = tui_textual.codex_model_options("gpt-custom")

        self.assertIn(("default", ""), options)
        self.assertIn(("gpt-visible", "gpt-visible"), options)
        self.assertIn(("gpt-custom", "gpt-custom"), options)
        self.assertNotIn(("gpt-hidden", "gpt-hidden"), options)

    def test_command_suggestions_only_for_slash_commands(self) -> None:
        self.assertEqual(command_suggestions("hello"), [])
        slash_commands = "\n".join(command_suggestions("/"))
        self.assertIn("/resume <n|session>", "\n".join(command_suggestions("/resume")))
        self.assertIn("/model <name|clear>", "\n".join(command_suggestions("/model")))
        self.assertIn("/bestby <duration|reasoning_tokens>", slash_commands)
        self.assertIn(
            "/keepworkspaces <on|off>",
            "\n".join(command_suggestions("/keep")),
        )
        self.assertIn(
            "/resumeinclude <on|off>",
            "\n".join(command_suggestions("/resumeinclude")),
        )
        self.assertEqual(len(command_suggestions("/")), tui_textual.MAX_SUGGESTIONS)

        descriptions = "\n".join(
            description for _command, description in tui_textual.TEXTUAL_COMMANDS
        )
        self.assertNotIn("same as", descriptions.lower())
        self.assertIn("limit how many agents may run concurrently", descriptions)
        self.assertIn("set the workspace PCR operates on", descriptions)
        self.assertNotIn("same as", tui_textual.build_help_text().lower())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_config_commands_update_cli_equivalents(self) -> None:
        args = parse_args([])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app._show_text = lambda _text: None

        app._handle_command("/maxparallel 2")
        app._handle_command("/serial")
        app._handle_command("/bestby duration")
        app._handle_command("/model gpt-5")
        app._handle_command("/syncback off")
        app._handle_command("/keepworkspaces on")
        app._handle_command("/resumeinclude off")

        self.assertEqual(app.args.max_parallel, 2)
        self.assertTrue(app.args.serial)
        self.assertEqual(app.args.best_by, "duration")
        self.assertEqual(app.args.model, "gpt-5")
        self.assertTrue(app.args.no_sync_back)
        self.assertTrue(app.args.keep_workspaces)
        self.assertFalse(app.args.resume_include_non_interactive)

        app._handle_command("/parallel")
        app._handle_command("/maxparallel auto")
        app._handle_command("/model clear")
        self.assertFalse(app.args.serial)
        self.assertIsNone(app.args.max_parallel)
        self.assertIsNone(app.args.model)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_config_controls_update_runner_settings(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    agents = app.query_one("#config-agents")
                    agents.value = "3"
                    agents.focus()
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(app.num_agents, 3)

                    max_parallel = app.query_one("#config-max-parallel")
                    max_parallel.value = "2"
                    max_parallel.focus()
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(app.args.max_parallel, 2)

                    for selector, value in (
                        ("#config-execution", "serial"),
                        ("#config-best-by", "duration"),
                        ("#config-sync-back", False),
                        ("#config-keep-workspaces", True),
                    ):
                        control = app.query_one(selector)
                        control.focus()
                        control.value = value
                        await pilot.pause()

                    self.assertTrue(app.args.serial)
                    self.assertEqual(app.args.best_by, "duration")
                    self.assertTrue(app.args.no_sync_back)
                    self.assertTrue(app.args.keep_workspaces)
                    self.assertGreaterEqual(len(app.command_history), 5)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_programmatic_select_updates_do_not_dispatch_commands(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            session = app_core.ResumeSession(
                session_id="session-1",
                title="previous question",
                cwd=str(Path.cwd()),
                updated_at=1,
            )
            async with app.run_test() as pilot:
                execution = app.query_one("#config-execution")
                execution.focus()
                with mock.patch.object(app, "_handle_execution") as handle_execution:
                    app._set_select_control(execution, "serial")
                    await pilot.pause()
                handle_execution.assert_not_called()

                resume = app.query_one("#config-resume")
                resume.focus()
                app.resume_session_id = "session-1"
                with mock.patch.object(app, "_handle_resume") as handle_resume:
                    for _ in range(20):
                        app._apply_resume_choices([session])
                    await pilot.pause()
                handle_resume.assert_not_called()
                self.assertEqual(resume.value, "session-1")

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_command_output_appends_after_conversation(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        app._sync = lambda: None
        pane = app.agents[1]
        pane.input_text = "existing question"
        pane.final_text = "existing answer"

        app._show_text("numofagents=5")
        app._show_text("execution=parallel")

        detail = app._detail_text()
        self.assertLess(detail.index("existing question"), detail.index("existing answer"))
        self.assertLess(detail.index("existing answer"), detail.index("numofagents=5"))
        self.assertLess(detail.index("numofagents=5"), detail.index("execution=parallel"))

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_copy_uses_selection_before_clear_or_exit(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    prompt = app.query_one("#prompt")
                    prompt.text = "copy me"
                    prompt.selection = ((0, 0), (0, 4))
                    prompt.focus()
                    copied: list[str] = []
                    app.copy_to_clipboard = copied.append

                    app.action_interrupt_or_exit()
                    await pilot.pause()

                    self.assertEqual(copied, ["copy"])
                    self.assertEqual(prompt.text, "copy me")
                    prompt.selection = ((0, 7), (0, 7))
                    execution = app.query_one("#config-execution")
                    execution.focus()
                    await pilot.pause()
                    app.action_interrupt_or_exit()
                    self.assertEqual(copied, ["copy", "parallel"])
                    self.assertTrue(app.query_one("#detail").allow_select)
                    self.assertTrue(app.query_one("#runner-workspace").allow_select)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_detail_selection_copies_rendered_content_before_prompt_selection(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(80, 30)) as pilot:
                pane = app.agents[1]
                pane.input_text = "detail question"
                pane.final_text = "detail answer"
                app._mark_detail_dirty(pane)
                app._sync()
                await pilot.pause()

                prompt = app.query_one("#prompt")
                prompt.text = "prompt text"
                prompt.selection = ((0, 0), (0, 6))
                prompt.focus()
                copied: list[str] = []
                app.copy_to_clipboard = copied.append

                self.assertTrue(await pilot.double_click("#detail", offset=(3, 0)))
                await pilot.pause()
                prompt.action_copy()

                self.assertEqual(len(copied), 1)
                self.assertIn("detail question", copied[0])
                self.assertIn("detail answer", copied[0])
                self.assertNotEqual(copied[0], "prompt")

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_copy_uses_pbcopy_on_macos(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        with mock.patch.object(tui_textual.sys, "platform", "darwin"):
            with mock.patch.object(tui_textual.shutil, "which", return_value="/usr/bin/pbcopy"):
                with mock.patch.object(tui_textual.subprocess, "run") as run_command:
                    app.copy_to_clipboard("可复制文本")

        self.assertEqual(app._clipboard, "可复制文本")
        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.kwargs["input"], "可复制文本")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_rejects_explicit_codex_subagent_resume(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            with mock.patch.object(
                tui_textual,
                "subagent_resume_error",
                return_value="Codex subagent cannot be resumed",
            ):
                async with app.run_test() as pilot:
                    app._handle_resume(["child-thread"])
                    for _ in range(20):
                        await pilot.pause()
                        if app.status == "Codex subagent cannot be resumed":
                            break

            self.assertEqual(app.resume_session_id, "")
            self.assertEqual(app.status, "Codex subagent cannot be resumed")

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_resume_selection_reuses_nonblocking_background_scan(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            session = app_core.ResumeSession(
                session_id="session-1",
                title="previous question",
                cwd=str(Path.cwd()),
                updated_at=1,
                rollout_path="/tmp/session-1.jsonl",
            )
            scan_started = threading.Event()
            release_scan = threading.Event()
            calls: list[Path] = []

            def slow_list(workspace: Path, **_kwargs: object) -> list[app_core.ResumeSession]:
                calls.append(workspace)
                scan_started.set()
                release_scan.wait(2)
                return [session]

            with mock.patch.object(tui_textual, "list_resume_sessions", side_effect=slow_list):
                with mock.patch.object(tui_textual, "subagent_resume_error", return_value=None):
                    with mock.patch.object(tui_textual, "load_codex_session_history", return_value=[]):
                        async with app.run_test() as pilot:
                            app._refresh_resume_control()
                            self.assertTrue(await asyncio.to_thread(scan_started.wait, 1))

                            app._handle_resume(["1"])

                            self.assertEqual(app.status, "Loading resume sessions")
                            self.assertEqual(app.pending_resume_selector, "1")
                            self.assertEqual(len(calls), 1)
                            release_scan.set()
                            for _ in range(30):
                                await pilot.pause()
                                if app.resume_session_id == "session-1":
                                    break

            self.assertEqual(app.resume_session_id, "session-1")
            self.assertEqual(len(calls), 1)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_resume_dropdown_loads_session_once_without_event_recursion(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            session = app_core.ResumeSession(
                session_id="session-1",
                title="previous question",
                cwd=str(Path.cwd()),
                updated_at=1,
                rollout_path="/tmp/session-1.jsonl",
            )
            with mock.patch.object(
                tui_textual,
                "list_resume_sessions",
                return_value=[session],
            ) as list_sessions:
                with mock.patch.object(
                    tui_textual,
                    "subagent_resume_error",
                    return_value=None,
                ) as validate_session:
                    with mock.patch.object(
                        tui_textual,
                        "load_codex_session_history",
                        return_value=[],
                    ) as load_history:
                        async with app.run_test() as pilot:
                            app._refresh_resume_control()
                            for _ in range(20):
                                await pilot.pause()
                                if app.resume_choices_loaded:
                                    break

                            resume = app.query_one("#config-resume")
                            resume.focus()
                            resume.value = "session-1"
                            for _ in range(30):
                                await pilot.pause()
                                if app.resume_session_id == "session-1":
                                    break
                            for _ in range(10):
                                await pilot.pause()

            self.assertEqual(app.resume_session_id, "session-1")
            list_sessions.assert_called_once()
            validate_session.assert_called_once()
            load_history.assert_called_once()

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_resume_loads_previous_conversation_into_detail(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                rollout = root / "sessions" / "rollout-session-1.jsonl"
                workspace.mkdir()
                rollout.parent.mkdir()
                records = [
                    {
                        "type": "session_meta",
                        "payload": {"id": "session-1", "cwd": str(workspace)},
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "previous question"},
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "agent_message", "message": "previous answer"},
                    },
                ]
                rollout.write_text(
                    "".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8",
                )
                args = parse_args(["--workspace", str(workspace)])
                app = tui_textual.PcrTextualApp(args)
                session = app_core.ResumeSession(
                    session_id="session-1",
                    title="previous question",
                    cwd=str(workspace),
                    updated_at=1,
                    rollout_path=str(rollout),
                )

                with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[session]):
                    with mock.patch.object(tui_textual, "get_codex_home", return_value=root):
                        with mock.patch.object(tui_textual, "subagent_resume_error", return_value=None):
                            async with app.run_test() as pilot:
                                app._handle_resume(["1"])
                                for _ in range(20):
                                    await pilot.pause()
                                    if "previous answer" in app._detail_text():
                                        break

                                detail = app._detail_text()
                                self.assertIn("> previous question", detail)
                                self.assertIn("✓ previous answer", detail)
                                self.assertTrue(app.query_one("#detail-frame").display)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_resume_control_dispatches_selected_session(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            session = app_core.ResumeSession(
                session_id="session-1",
                title="previous question",
                cwd=str(Path.cwd()),
                updated_at=1,
            )
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    app._apply_resume_choices([session])
                    resume = app.query_one("#config-resume")
                    resume.focus()
                    await pilot.pause()
                    with mock.patch.object(app, "_handle_resume") as handle_resume:
                        resume.value = "session-1"
                        await pilot.pause()

                    handle_resume.assert_called_once_with(["session-1"])

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_path_and_promptfile_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            prompt_file = root / "prompt.txt"
            prompt_file.write_text("hello from file\n", encoding="utf-8")
            prompts = []
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            app._sync = lambda: None
            app._show_text = lambda _text: None
            app._start_run = prompts.append

            app._handle_command(f"/workspace {workspace}")
            app._handle_command(f"/runsdir {root / 'runs'}")
            app._handle_command("/codexbin /usr/local/bin/codex")
            app._handle_command(f"/promptfile {prompt_file}")

            self.assertEqual(app.workspace, workspace.resolve())
            self.assertEqual(app.args.workspace, str(workspace.resolve()))
            self.assertEqual(app.args.runs_dir, str(root / "runs"))
            self.assertEqual(app.args.codex_bin, "/usr/local/bin/codex")
            self.assertEqual(prompts, ["hello from file"])

    def test_display_line_filters_lifecycle_noise(self) -> None:
        self.assertEqual(display_line_from_output('{"type":"thread.started","thread_id":"abc"}'), "")
        self.assertEqual(display_line_from_output('{"type":"agent_reasoning","text":"thinking"}'), "thinking")
        self.assertEqual(display_line_from_output("agent_reasoning:thinking"), "thinking")
        self.assertEqual(display_line_from_output("2026-07-07 ERROR codex_models_manager: timeout"), "")
        self.assertEqual(display_line_from_output("2026-07-07 ERROR codex models_manager: timeout"), "")
        self.assertEqual(display_line_from_output("error=apply_patch verification failed"), "")
        self.assertEqual(display_line_from_output("Failed to find expected lines"), "")
        self.assertEqual(display_line_from_output("Run result:"), "")
        self.assertEqual(display_line_from_output("2026-07-07 Run result:"), "")
        self.assertEqual(display_line_from_output("- best agent: agent_005"), "")
        self.assertEqual(display_line_from_output('{"payload":{"item":{"text":"real content"}}}'), "real content")
        self.assertEqual(
            display_line_from_output(json.dumps({"type": "agent_message", "text": "the best agent approach is to compare outputs"})),
            "the best agent approach is to compare outputs",
        )

    def test_display_line_keeps_reasoning_and_output_separate(self) -> None:
        self.assertEqual(
            display_line_parts_from_output('{"type":"agent_reasoning","text":"thinking"}'),
            ("thought", "thinking"),
        )
        self.assertEqual(
            display_line_parts_from_output('{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}'),
            ("output", "answer"),
        )

    def test_display_line_keeps_full_long_content(self) -> None:
        long_text = "alpha " + ("x" * 1200) + "\nsecond line"

        self.assertEqual(display_line_from_output(json.dumps({"type": "agent_message", "text": long_text})), long_text)

    def test_display_line_shows_command_execution_from_stdout_log(self) -> None:
        started = {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "command": "/bin/zsh -lc 'pytest -q'",
                "aggregated_output": "",
                "status": "in_progress",
            },
        }
        completed = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/zsh -lc 'pytest -q'",
                "aggregated_output": "one\ntwo\n",
                "exit_code": 0,
                "status": "completed",
            },
        }

        self.assertEqual(display_line_parts_from_output(json.dumps(started)), ("activity", "$ /bin/zsh -lc 'pytest -q'"))
        self.assertEqual(
            display_line_parts_from_output(json.dumps(completed)),
            ("activity", "$ /bin/zsh -lc 'pytest -q' [exit 0]\none\ntwo"),
        )

    def test_display_line_compacts_long_command_output(self) -> None:
        completed = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/zsh -lc 'pytest -q'",
                "aggregated_output": "one\ntwo\nthree\nfour\nfive\n",
                "exit_code": 0,
                "status": "completed",
            },
        }

        self.assertEqual(
            display_line_parts_from_output(json.dumps(completed)),
            ("activity", "$ /bin/zsh -lc 'pytest -q' [exit 0]\none\ntwo\n...\nfive"),
        )

    def test_display_line_compacts_very_long_command_output_line(self) -> None:
        long_line = "a" * 3000
        completed = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "python big.py",
                "aggregated_output": long_line,
                "exit_code": 1,
                "status": "completed",
            },
        }

        category, text = display_line_parts_from_output(json.dumps(completed))

        self.assertEqual(category, "activity")
        self.assertIn("$ python big.py [exit 1]", text)
        self.assertLess(len(text), len(long_line))
        self.assertIn(" ... ", text)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_finalize_selected_agent_sets_resume_and_syncs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "run" / "workspaces" / "agent_002"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            args = parse_args([])
            args.workspace = str(workspace)
            app = tui_textual.PcrTextualApp(args)
            workspaces_root = root / "run" / "workspaces"
            app.pending_workspaces_root = workspaces_root
            app.agents[2].result = {
                "idx": 2,
                "workspace_dir": str(candidate),
                "meta_dir": "",
                "codex_home": "",
                "stdout_log": "",
                "stderr_log": "",
                "final_message": "",
                "command": [],
                "returncode": 0,
                "status": "success",
                "seconds": 1.0,
                "codex_thread_id": "session-2",
                "reasoning_tokens": 10,
                "reasoning_token_values": [10],
                "error": None,
                "stdout_tail": "",
                "stderr_tail": "",
            }

            calls = []

            def sync_side_effect(_candidate: Path, _workspace: Path) -> None:
                calls.append("sync")

            def promote_side_effect(result: AgentResult, _workspace: Path) -> AgentResult:
                calls.append("promote")
                return result

            with mock.patch.object(tui_textual, "promote_best_codex_session_to_workspace") as promote:
                with mock.patch.object(tui_textual, "sync_best_workspace_back") as sync_back:
                    with mock.patch.object(tui_textual, "cleanup_workspace_copies") as cleanup:
                        sync_back.side_effect = sync_side_effect
                        promote.side_effect = promote_side_effect

                        self.assertTrue(app._finalize_agent(2))

            self.assertEqual(app.resume_session_id, "session-2")
            self.assertEqual(calls, ["sync", "promote"])
            sync_back.assert_called_once_with(candidate, workspace.resolve())
            cleanup.assert_called_once_with(workspace.resolve(), workspaces_root)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_start_run_continues_from_selected_agent_not_best_agent(self) -> None:
        args = parse_args([])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.best_agent = 5
        app.selected_agent = 2

        with mock.patch.object(app, "_finalize_agent", return_value=True) as finalize:
            with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                thread_cls.return_value.start.return_value = None
                app._start_run("next question")

        finalize.assert_called_once_with(2, archive_detail=True)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_can_stop_remaining_agents_and_continue_from_finished_selection(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "3"]))
        app._sync = lambda: None
        app.running = True
        app.cancel_event = threading.Event()
        app.selected_agent = 2
        app.agents[2].result = {"status": "success"}

        app._start_run("follow-up question")

        self.assertTrue(app.cancel_event.is_set())
        self.assertEqual(app.queued_prompt, "follow-up question")
        self.assertEqual(app.queued_agent, 2)

        continued: list[str] = []
        with mock.patch.object(app, "_finalize_agent", return_value=True) as finalize:
            with mock.patch.object(app, "_start_run", side_effect=continued.append):
                app._on_runner_event(
                    tui_textual.RunnerEvent(
                        {
                            "type": "run_finished",
                            "run_root": "/tmp/pcr-test/run",
                            "best_agent": None,
                            "cancelled": True,
                        }
                    )
                )

        finalize.assert_called_once_with(2, archive_detail=True)
        self.assertEqual(continued, ["follow-up question"])
        self.assertEqual(app.queued_prompt, "")
        self.assertIsNone(app.queued_agent)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_keeps_follow_up_text_when_selected_agent_is_still_running(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    app.running = True
                    prompt = app.query_one("#prompt")
                    prompt.text = "follow-up question"
                    prompt.focus()
                    await pilot.press("enter")
                    await pilot.pause()

                    self.assertEqual(prompt.text, "follow-up question")
                    self.assertIn("has not finished successfully", app.status)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_run_finished_does_not_auto_switch_to_best_agent(self) -> None:
        args = parse_args(["-n", "5"])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app.selected_agent = 2

        app._on_runner_event(tui_textual.RunnerEvent({"type": "run_finished", "run_root": "/tmp/pcr-test/run", "best_agent": 5}))

        self.assertEqual(app.selected_agent, 2)
        self.assertEqual(app.best_agent, 5)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_start_run_discards_no_success_pending_run(self) -> None:
        args = parse_args([])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.best_agent = None

        def discard_side_effect() -> bool:
            app._clear_pending_run()
            return True

        with mock.patch.object(app, "_discard_pending_run", side_effect=discard_side_effect) as discard:
            with mock.patch.object(app, "_finalize_agent", return_value=False) as finalize:
                with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                    thread_cls.return_value.start.return_value = None
                    app._start_run("next question")

        discard.assert_called_once_with()
        finalize.assert_not_called()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_resume_clear_finalizes_pending_without_resuming(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "run" / "workspaces" / "agent_002"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            args = parse_args([])
            args.workspace = str(workspace)
            app = tui_textual.PcrTextualApp(args)
            app._sync = lambda: None
            app.pending_workspaces_root = root / "run" / "workspaces"
            app.best_agent = 2
            app.selected_agent = 2
            app.resume_session_id = "old-session"
            app.agents[2].result = {
                "idx": 2,
                "workspace_dir": str(candidate),
                "meta_dir": "",
                "codex_home": "",
                "stdout_log": "",
                "stderr_log": "",
                "final_message": "",
                "command": [],
                "returncode": 0,
                "status": "success",
                "seconds": 1.0,
                "codex_thread_id": "session-2",
                "reasoning_tokens": 10,
                "reasoning_token_values": [10],
                "error": None,
                "stdout_tail": "",
                "stderr_tail": "",
            }

            with mock.patch.object(tui_textual, "promote_best_codex_session_to_workspace") as promote:
                with mock.patch.object(tui_textual, "sync_best_workspace_back") as sync_back:
                    with mock.patch.object(tui_textual, "cleanup_workspace_copies") as cleanup:
                        promote.side_effect = lambda result, _workspace: result
                        app._handle_resume(["clear"])

            self.assertEqual(app.resume_session_id, "")
            self.assertFalse(app._has_pending_run())
            self.assertEqual(app.status, "Resume cleared")
            sync_back.assert_called_once_with(candidate, workspace.resolve())
            cleanup.assert_called_once_with(workspace.resolve(), root / "run" / "workspaces")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_finalize_archives_selected_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "run" / "workspaces" / "agent_002"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            args = parse_args([])
            args.workspace = str(workspace)
            app = tui_textual.PcrTextualApp(args)
            app.pending_workspaces_root = root / "run" / "workspaces"
            pane = app.agents[2]
            pane.input_text = "first question"
            pane.thought_lines = ["thought"]
            pane.output_lines = ["draft answer"]
            pane.final_text = "final answer"
            pane.result = {
                "idx": 2,
                "workspace_dir": str(candidate),
                "meta_dir": "",
                "codex_home": "",
                "stdout_log": "",
                "stderr_log": "",
                "final_message": "",
                "command": [],
                "returncode": 0,
                "status": "success",
                "seconds": 1.0,
                "codex_thread_id": "session-2",
                "reasoning_tokens": 10,
                "reasoning_token_values": [10],
                "error": None,
                "stdout_tail": "",
                "stderr_tail": "",
            }

            with mock.patch.object(tui_textual, "promote_best_codex_session_to_workspace") as promote:
                with mock.patch.object(tui_textual, "sync_best_workspace_back"):
                    with mock.patch.object(tui_textual, "cleanup_workspace_copies"):
                        promote.side_effect = lambda result, _workspace: result
                        self.assertTrue(app._finalize_agent(2, archive_detail=True))

            history_text = "\n".join(block for _prefix, block, _style in app.detail_history)
            self.assertIn("first question", history_text)
            self.assertIn("thought", history_text)
            self.assertIn("draft answer", history_text)
            self.assertIn("final answer", history_text)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_left_right_switches_when_detail_scroll_has_focus(self) -> None:
        async def run() -> None:
            args = parse_args(["-n", "3"])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test() as pilot:
                app.query_one("#detail-scroll").focus()
                await pilot.pause()
                await pilot.press("right")

                self.assertEqual(app.selected_agent, 2)
                self.assertEqual(getattr(app.focused, "id", None), "prompt")

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_prompt_left_right_switches_agent_once(self) -> None:
        async def run() -> None:
            args = parse_args(["-n", "5"])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test() as pilot:
                app.query_one("#prompt").focus()
                await pilot.pause()
                await pilot.press("right")

                self.assertEqual(app.selected_agent, 2)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_prompt_left_right_moves_cursor_once_when_text_exists(self) -> None:
        async def run() -> None:
            args = parse_args(["-n", "5"])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test() as pilot:
                prompt = app.query_one("#prompt")
                prompt.focus()
                await pilot.press("a")
                await pilot.press("b")
                await pilot.press("c")
                await pilot.pause()

                self.assertEqual(prompt.cursor_location, (0, 3))
                await pilot.press("left")
                await pilot.pause()
                self.assertEqual(prompt.cursor_location, (0, 2))
                await pilot.press("right")
                await pilot.pause()
                self.assertEqual(prompt.cursor_location, (0, 3))
                self.assertEqual(app.selected_agent, 1)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_detail_frame_title_stays_outside_scroll_body(self) -> None:
        async def run() -> None:
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test() as pilot:
                await pilot.pause()
                frame = app.query_one("#detail-frame")

                self.assertFalse(frame.display)
                app.agents[1].input_text = "hello"
                app._mark_detail_dirty(app.agents[1])
                app._sync()
                await pilot.pause()

                self.assertTrue(frame.display)
                self.assertEqual(frame.border_title, "AGENT-001, ←/→ switch")
                app.agents[1].result = {"seconds": 1.234, "reasoning_tokens": 42}
                app.agents[1].reasoning_tokens = 42
                app._sync()
                await pilot.pause()

                self.assertEqual(frame.border_title, "AGENT-001, seconds=1.23s, reasoning_tokens=42, ←/→ switch")

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_runner_panel_has_title_and_editable_controls(self) -> None:
        async def run() -> None:
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one("#runner-frame")

                self.assertEqual(panel.border_title, "PARALLEL-CODEX-RUNNER")
                self.assertTrue(panel.styles.border_title_style.bold)
                self.assertEqual(app.query_one("#config-agents").value, "5")
                self.assertEqual(app.query_one("#config-max-parallel").value, "5")
                self.assertEqual(app.query_one("#config-execution").value, "parallel")
                self.assertEqual(app.query_one("#config-best-by").value, "reasoning_tokens")
                self.assertNotIn("METADATA", [label for label, _value in app._tree_rows()])
                self.assertNotIn("WORKSPACE COPIES", [label for label, _value in app._tree_rows()])
                self.assertNotIn("MODULE_DIR", [label for label, _value in app._tree_rows()])
                self.assertNotIn("RUN_ANCHOR", [label for label, _value in app._tree_rows()])
                self.assertEqual(len(app.query("#runner-module-dir")), 0)
                self.assertEqual(len(app.query("#runner-run-anchor")), 0)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_routes_typing_to_prompt_except_runner_inputs(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(100, 40)) as pilot:
                prompt = app.query_one("#prompt")
                app.screen.set_focus(None)
                await pilot.press("你")
                await pilot.pause()

                self.assertEqual(prompt.text, "你")
                self.assertIs(app.focused, prompt)

                agents = app.query_one("#config-agents")
                agents.value = ""
                agents.focus()
                await pilot.press("7")
                await pilot.pause()

                self.assertEqual(agents.value, "7")
                self.assertEqual(prompt.text, "你")

                app.agents[1].input_text = "clickable detail"
                app._mark_detail_dirty(app.agents[1])
                app._sync()
                await pilot.pause()
                self.assertTrue(await pilot.click("#detail", offset=(3, 0)))
                await pilot.press("好")
                await pilot.pause()

                self.assertEqual(prompt.text, "你好")
                self.assertIs(app.focused, prompt)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_runner_panel_does_not_hide_detail_in_narrow_terminal(self) -> None:
        async def run() -> None:
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test(size=(80, 30)) as pilot:
                app.agents[1].input_text = "hello"
                app._mark_detail_dirty(app.agents[1])
                app._sync()
                await pilot.pause()

                self.assertGreater(app.query_one("#detail-frame").size.height, 0)
                self.assertGreaterEqual(app.query_one("#prompt").region.height, 3)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_detail_refresh_preserves_manual_scroll_position(self) -> None:
        async def run() -> None:
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test(size=(80, 40)) as pilot:
                pane = app.agents[1]
                pane.output_lines = [f"line {idx}" for idx in range(120)]
                app._mark_detail_dirty(pane)
                app._sync()
                scroll = app.query_one("#detail-scroll")
                await pilot.pause()
                scroll.scroll_end(animate=False, immediate=True)
                await pilot.pause()
                self.assertGreater(scroll.scroll_y, 0)

                scroll.scroll_home(animate=False, immediate=True)
                await pilot.pause()
                self.assertEqual(scroll.scroll_y, 0)

                pane.output_lines.append("new line")
                app._mark_detail_dirty(pane)
                app._sync()
                await pilot.pause()

                self.assertEqual(scroll.scroll_y, 0)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_detail_refresh_follows_when_already_at_bottom(self) -> None:
        async def run() -> None:
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test(size=(80, 40)) as pilot:
                pane = app.agents[1]
                pane.output_lines = [f"line {idx}" for idx in range(80)]
                app._mark_detail_dirty(pane)
                app._sync()
                scroll = app.query_one("#detail-scroll")
                await pilot.pause()
                scroll.scroll_end(animate=False, immediate=True)
                await pilot.pause()

                pane.output_lines.extend(f"new line {idx}" for idx in range(30))
                app._mark_detail_dirty(pane)
                app._sync()
                await pilot.pause()

                self.assertTrue(scroll.is_vertical_scroll_end)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_status_during_run_does_not_trap_manual_detail_scroll(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(80, 40)) as pilot:
                pane = app.agents[1]
                pane.status = "running"
                pane.output_lines = [f"line {idx}" for idx in range(120)]
                app.running = True
                app._mark_detail_dirty(pane)
                app._sync()
                scroll = app.query_one("#detail-scroll")
                await pilot.pause()
                scroll.scroll_end(animate=False, immediate=True)
                await pilot.pause()

                app._handle_command("/status")
                scroll.scroll_up(animate=True)
                self.assertFalse(scroll.follow_tail)

                pane.output_lines.append("new live line")
                app._mark_detail_dirty(pane)
                app._sync()
                await pilot.pause()
                await pilot.pause()

                self.assertFalse(scroll.follow_tail)
                self.assertFalse(scroll.is_vertical_scroll_end)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_prompt_backspace_deletes_one_character(self) -> None:
        async def run() -> None:
            args = parse_args([])
            app = tui_textual.PcrTextualApp(args)
            async with app.run_test() as pilot:
                prompt = app.query_one("#prompt")
                prompt.focus()
                await pilot.press("a")
                await pilot.press("b")
                await pilot.press("c")
                await pilot.press("backspace")

                self.assertEqual(prompt.text, "ab")

        asyncio.run(run())


class StreamLogTests(unittest.TestCase):
    def test_stream_to_log_handles_long_json_line(self) -> None:
        async def run() -> None:
            reader = asyncio.StreamReader(limit=8)
            line = json.dumps({"type": "thread.started", "thread_id": "abc", "message": "x" * 100}).encode() + b"\n"
            reader.feed_data(line)
            reader.feed_eof()
            state = AgentState(idx=1)
            with tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / "stdout.log"
                await stream_to_log(reader, log_path, state, "stdout")
                self.assertEqual(log_path.read_bytes(), line)
                self.assertEqual(state.codex_thread_id, "abc")

        asyncio.run(run())

    def test_stream_to_log_sends_compact_command_output_to_progress(self) -> None:
        async def run() -> None:
            raw = {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "pytest -q",
                    "aggregated_output": "one\ntwo\nthree\nfour\nfive\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
            raw_line = json.dumps(raw).encode() + b"\n"
            reader = asyncio.StreamReader(limit=8)
            reader.feed_data(raw_line)
            reader.feed_eof()
            events = []
            state = AgentState(idx=1)
            with tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / "stdout.log"
                await stream_to_log(reader, log_path, state, "stdout", events.append)

                self.assertEqual(log_path.read_bytes(), raw_line)

            line_events = [event for event in events if event["type"] == "agent_line"]
            self.assertEqual(len(line_events), 1)
            compact_text = line_events[0]["text"]
            self.assertLess(len(compact_text), len(raw_line.decode()))
            self.assertEqual(
                display_line_parts_from_output(compact_text),
                ("activity", "$ pytest -q [exit 0]\none\ntwo\n...\nfive"),
            )

        asyncio.run(run())


class AgentCancelTests(unittest.TestCase):
    def test_run_one_agent_scrubs_codex_support_entries_after_process_exit(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                meta = root / "meta"
                codex_home = root / "codex_home"
                workspace.mkdir()
                codex_home.mkdir()
                (codex_home / "auth.json").write_text("secret", encoding="utf-8")
                (codex_home / "state_5.sqlite").write_text("state", encoding="utf-8")

                result = await run_one_agent(
                    idx=1,
                    agent_workspace=workspace,
                    meta_dir=meta,
                    codex_home=codex_home,
                    prompt="hello",
                    command=[sys.executable, "-c", "import sys; sys.stdin.read(); print('done')"],
                )

                self.assertEqual(result.status, "success")
                self.assertFalse((codex_home / "auth.json").exists())
                self.assertTrue((codex_home / "state_5.sqlite").exists())

        asyncio.run(run())

    def test_run_one_agent_stops_process_on_cancel(self) -> None:
        async def run() -> None:
            cancel_event = threading.Event()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                meta = root / "meta"
                codex_home = root / "codex_home"
                workspace.mkdir()
                codex_home.mkdir()

                async def cancel_soon() -> None:
                    await asyncio.sleep(0.1)
                    cancel_event.set()

                canceller = asyncio.create_task(cancel_soon())
                result = await run_one_agent(
                    idx=1,
                    agent_workspace=workspace,
                    meta_dir=meta,
                    codex_home=codex_home,
                    prompt="",
                    command=[sys.executable, "-c", "import time; time.sleep(10)"],
                    cancel_event=cancel_event,
                )
                await canceller

            self.assertEqual(result.status, "cancelled")
            self.assertLess(result.seconds, 3)

        asyncio.run(run())


class ResumeSessionTests(unittest.TestCase):
    def make_result(self, idx: int, workspace: Path, session_id: str) -> AgentResult:
        return AgentResult(
            idx=idx,
            workspace_dir=str(workspace),
            meta_dir="",
            codex_home=str(workspace.parent / f"codex_home_{idx}"),
            stdout_log="",
            stderr_log="",
            final_message="",
            command=[],
            returncode=0,
            status="success",
            seconds=1.0,
            codex_thread_id=session_id,
        )

    def test_extract_codex_thread_id_from_thread_started_event(self) -> None:
        self.assertEqual(
            extract_codex_thread_id_from_json({"type": "thread.started", "thread_id": "019f-test"}),
            "019f-test",
        )

    def test_load_codex_session_history_prefers_clean_events_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "sessions" / "rollout-session-1.jsonl"
            rollout.parent.mkdir()
            records = [
                {"type": "session_meta", "payload": {"id": "session-1", "cwd": "/workspace"}},
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-1"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "<environment_context>hidden</environment_context>"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "previous question"}],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "previous question"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "checked the project\n\n<!-- -->"}],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_reasoning", "text": "checked the project\n\n<!-- -->"},
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "previous answer"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "previous answer"}],
                    },
                },
            ]
            rollout.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            history = load_codex_session_history(root, "session-1", rollout)

            self.assertEqual(
                [(entry.category, entry.text) for entry in history],
                [
                    ("user", "previous question"),
                    ("thought", "checked the project"),
                    ("output", "previous answer"),
                ],
            )

    def test_load_codex_session_history_falls_back_to_response_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "rollout-session-1.jsonl"
            records = [
                {"type": "session_meta", "payload": {"session_id": "session-1"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "fallback question"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "fallback answer"}],
                    },
                },
            ]
            rollout.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            history = load_codex_session_history(root, "session-1", rollout)

            self.assertEqual(
                [(entry.category, entry.text) for entry in history],
                [("user", "fallback question"), ("output", "fallback answer")],
            )

    def test_codex_sqlite_access_is_serialized_across_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            conn = sqlite3.connect(root / "state_5.sqlite")
            try:
                conn.execute(
                    "CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, source TEXT)"
                )
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, ?)",
                    ("session-1", str(workspace), "cli"),
                )
                conn.commit()
            finally:
                conn.close()

            original_connect = sqlite3.connect
            counter_lock = threading.Lock()
            active = 0
            max_active = 0
            start = threading.Barrier(3)

            def slow_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
                nonlocal active, max_active
                with counter_lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.05)
                    return original_connect(*args, **kwargs)
                finally:
                    with counter_lock:
                        active -= 1

            def load_sessions() -> None:
                start.wait()
                app_core.load_resume_sessions_from_state(root, workspace, True)

            def inspect_session() -> None:
                start.wait()
                app_core.subagent_resume_error(root, "session-1")

            with mock.patch.object(app_core.sqlite3, "connect", side_effect=slow_connect):
                first = threading.Thread(target=load_sessions)
                second = threading.Thread(target=inspect_session)
                first.start()
                second.start()
                start.wait()
                first.join(2)
                second.join(2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(max_active, 1)

    def test_state_loader_filters_workspace_archived_and_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "state_5.sqlite"
            workspace = root / "workspace"
            workspace.mkdir()
            other = root / "other"
            other.mkdir()

            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    CREATE TABLE threads (
                        id TEXT PRIMARY KEY,
                        cwd TEXT NOT NULL,
                        title TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        recency_at INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        model TEXT,
                        rollout_path TEXT,
                        tokens_used INTEGER NOT NULL,
                        archived INTEGER NOT NULL
                    )
                    """
                )
                rows = [
                    ("interactive", str(workspace), "Interactive title", 1, 20, 20, "cli", "gpt-5", "a.jsonl", 100, 0),
                    ("exec", str(workspace), "Exec title", 1, 30, 30, "exec", "gpt-5", "b.jsonl", 200, 0),
                    ("archived", str(workspace), "Archived title", 1, 40, 40, "cli", "gpt-5", "c.jsonl", 300, 1),
                    ("other", str(other), "Other title", 1, 50, 50, "cli", "gpt-5", "d.jsonl", 400, 0),
                ]
                conn.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                conn.commit()
            finally:
                conn.close()

            default_sessions = load_resume_sessions_from_state(root, workspace)
            all_sessions = load_resume_sessions_from_state(root, workspace, include_non_interactive=True)

            self.assertEqual([s.session_id for s in default_sessions], ["interactive"])
            self.assertEqual([s.session_id for s in all_sessions], ["exec", "interactive"])

    def test_state_loader_excludes_and_rejects_codex_v2_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            child_id = "019f-child"
            parent_id = "019f-parent"
            child_source = json.dumps(
                {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_id,
                            "depth": 1,
                        }
                    }
                }
            )

            conn = sqlite3.connect(root / "state_5.sqlite")
            try:
                conn.execute(
                    """
                    CREATE TABLE threads (
                        id TEXT PRIMARY KEY,
                        cwd TEXT NOT NULL,
                        title TEXT NOT NULL,
                        updated_at INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        thread_source TEXT,
                        archived INTEGER NOT NULL
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (parent_id, str(workspace), "Parent", 10, "vscode", "user", 0),
                        (child_id, str(workspace), "Child", 20, child_source, "subagent", 0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            self.assertEqual(
                [session.session_id for session in load_resume_sessions_from_state(root, workspace)],
                [parent_id],
            )
            self.assertEqual(
                [
                    session.session_id
                    for session in load_resume_sessions_from_state(
                        root,
                        workspace,
                        include_non_interactive=True,
                    )
                ],
                [parent_id],
            )

            error = app_core.subagent_resume_error(root, child_id)
            self.assertIsNotNone(error)
            self.assertIn(parent_id, error or "")

            args = mock.Mock(resume=False, resume_session_id=child_id)
            with mock.patch.object(app_core, "get_codex_home", return_value=root):
                with self.assertRaisesRegex(SystemExit, parent_id):
                    app_core.resolve_resume_session(args, workspace)

            target_workspace = root / "target"
            target_workspace.mkdir()
            promotion = promote_codex_session_to_workspace(root, child_id, target_workspace)
            self.assertIn(parent_id, promotion.error or "")
            conn = sqlite3.connect(root / "state_5.sqlite")
            try:
                child_cwd = conn.execute(
                    "SELECT cwd FROM threads WHERE id = ?",
                    (child_id,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(child_cwd, str(workspace))

    def test_jsonl_loader_excludes_codex_v2_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            sessions = root / "sessions" / "2026" / "07" / "11"
            workspace.mkdir()
            sessions.mkdir(parents=True)
            parent_id = "019f-parent"
            child_id = "019f-child"

            parent_meta = {
                "type": "session_meta",
                "payload": {
                    "session_id": parent_id,
                    "id": parent_id,
                    "cwd": str(workspace),
                    "source": "vscode",
                    "thread_source": "user",
                },
            }
            child_meta = {
                "type": "session_meta",
                "payload": {
                    "session_id": parent_id,
                    "id": child_id,
                    "parent_thread_id": parent_id,
                    "cwd": str(workspace),
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": parent_id,
                                "depth": 1,
                            }
                        }
                    },
                    "thread_source": "subagent",
                },
            }
            (sessions / f"rollout-{parent_id}.jsonl").write_text(
                json.dumps(parent_meta) + "\n",
                encoding="utf-8",
            )
            (sessions / f"rollout-{child_id}.jsonl").write_text(
                json.dumps(child_meta) + "\n",
                encoding="utf-8",
            )

            loaded = app_core.load_resume_sessions_from_jsonl(root, workspace)
            self.assertEqual([session.session_id for session in loaded], [parent_id])
            self.assertIn(parent_id, app_core.subagent_resume_error(root, child_id) or "")

    def test_prepare_agent_codex_home_copies_support_entries_without_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_home = root / "real"
            agent_home = root / "agent"
            agent_workspace = root / "workspaces" / "agent_001"
            (real_home / "profiles").mkdir(parents=True)
            agent_workspace.mkdir(parents=True)
            (real_home / "config.toml").write_text("approval_policy = 'never'\n", encoding="utf-8")
            (real_home / "profiles" / "default.toml").write_text("model = 'gpt-5'\n", encoding="utf-8")
            (real_home / "history.jsonl").write_text("{}\n", encoding="utf-8")
            (real_home / "sessions").mkdir()
            (real_home / "plugins").mkdir()
            (real_home / "plugins" / "cache.bin").write_text("large", encoding="utf-8")
            (real_home / "random").mkdir()
            (real_home / "random" / "note.txt").write_text("ignored", encoding="utf-8")

            prepare_agent_codex_home(real_home, agent_home, agent_workspace, None)

            self.assertFalse((agent_home / "config.toml").is_symlink())
            self.assertFalse((agent_home / "profiles").is_symlink())
            self.assertFalse((agent_home / "profiles" / "default.toml").is_symlink())
            self.assertEqual((agent_home / "config.toml").read_text(encoding="utf-8"), "approval_policy = 'never'\n")
            self.assertEqual((agent_home / "profiles" / "default.toml").read_text(encoding="utf-8"), "model = 'gpt-5'\n")
            self.assertFalse((agent_home / "history.jsonl").exists())
            self.assertFalse((agent_home / "sessions").exists())
            self.assertFalse((agent_home / "plugins").exists())
            self.assertFalse((agent_home / "random").exists())

            (agent_home / "config.toml").write_text("changed\n", encoding="utf-8")
            self.assertEqual((real_home / "config.toml").read_text(encoding="utf-8"), "approval_policy = 'never'\n")

    def test_scrub_codex_home_removes_support_files_but_keeps_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "codex_home"
            (home / "sessions").mkdir(parents=True)
            (home / "sessions" / "rollout.jsonl").write_text("{}\n", encoding="utf-8")
            (home / "state_5.sqlite").write_text("state", encoding="utf-8")
            (home / "auth.json").write_text("secret", encoding="utf-8")
            (home / "config.toml").write_text("secret", encoding="utf-8")
            (home / "profiles").mkdir()
            (home / "profiles" / "default.toml").write_text("secret", encoding="utf-8")

            removed = scrub_codex_home_support_entries(home)

            self.assertIn("auth.json", removed)
            self.assertIn("config.toml", removed)
            self.assertIn("profiles", removed)
            self.assertTrue((home / "sessions" / "rollout.jsonl").exists())
            self.assertTrue((home / "state_5.sqlite").exists())
            self.assertFalse((home / "auth.json").exists())

    def test_prepare_agent_codex_home_rebinds_cwd_when_rollout_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_home = root / "real"
            agent_home = root / "agent"
            agent_workspace = root / "workspaces" / "agent_001"
            real_home.mkdir()
            agent_workspace.mkdir(parents=True)

            session_id = "019f-missing-rollout"
            missing_rollout = real_home / "sessions" / "missing.jsonl"
            conn = sqlite3.connect(real_home / "state_5.sqlite")
            try:
                conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, rollout_path TEXT NOT NULL)")
                conn.execute("INSERT INTO threads VALUES (?, ?, ?)", (session_id, "/old/workspace", str(missing_rollout)))
                conn.commit()
            finally:
                conn.close()

            prepare_agent_codex_home(real_home, agent_home, agent_workspace, session_id)

            conn = sqlite3.connect(agent_home / "state_5.sqlite")
            try:
                row = conn.execute("SELECT cwd, rollout_path FROM threads WHERE id = ?", (session_id,)).fetchone()
            finally:
                conn.close()

            self.assertEqual(row, (str(agent_workspace), str(missing_rollout)))

    def test_promote_codex_session_rebinds_state_and_rollout_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "state_5.sqlite"
            sessions_dir = root / "sessions" / "2026" / "07" / "07"
            sessions_dir.mkdir(parents=True)
            workspace = root / "workspace"
            agent_workspace = root / "runs" / "agent_001"
            workspace.mkdir()
            agent_workspace.mkdir(parents=True)

            session_id = "019f-promote-test"
            rollout = sessions_dir / f"rollout-2026-07-07T00-00-00-{session_id}.jsonl"
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "session_id": session_id,
                            "id": session_id,
                            "cwd": str(agent_workspace),
                            "source": "exec",
                            "originator": "codex_exec",
                            "thread_source": "user",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    """
                    CREATE TABLE threads (
                        id TEXT PRIMARY KEY,
                        cwd TEXT NOT NULL,
                        rollout_path TEXT NOT NULL,
                        source TEXT NOT NULL,
                        thread_source TEXT,
                        updated_at INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        recency_at INTEGER NOT NULL,
                        recency_at_ms INTEGER NOT NULL,
                        archived INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, str(agent_workspace), str(rollout), "exec", "user", 20, 20000, 1, 1000, 0),
                )
                conn.commit()
            finally:
                conn.close()

            promotion = promote_codex_session_to_workspace(root, session_id, workspace)

            self.assertTrue(promotion.state_found)
            self.assertTrue(promotion.state_updated)
            self.assertTrue(promotion.rollout_updated)
            self.assertTrue(promotion.source_promoted)
            promoted_sessions = load_resume_sessions_from_state(root, workspace)
            self.assertEqual([s.session_id for s in promoted_sessions], [session_id])

            conn = sqlite3.connect(db)
            try:
                row = conn.execute("SELECT cwd, source, recency_at, recency_at_ms FROM threads WHERE id = ?", (session_id,)).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, (str(workspace.resolve()), "cli", 20, 20000))

            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])["payload"]
            self.assertEqual(meta["cwd"], str(workspace.resolve()))
            self.assertEqual(meta["source"], "cli")
            self.assertEqual(meta["originator"], "codex-tui")

    def test_import_codex_session_from_isolated_home_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_home = root / "real_codex"
            isolated_home = root / "isolated_codex"
            workspace = root / "workspace"
            agent_workspace = root / "runs" / "workspaces" / "agent_001"
            real_home.mkdir()
            isolated_home.mkdir()
            workspace.mkdir()
            agent_workspace.mkdir(parents=True)

            session_id = "019f-import-test"
            isolated_sessions = isolated_home / "sessions" / "2026" / "07" / "07"
            isolated_sessions.mkdir(parents=True)
            isolated_rollout = isolated_sessions / f"rollout-2026-07-07T00-00-00-{session_id}.jsonl"
            isolated_rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "session_id": session_id,
                            "id": session_id,
                            "cwd": str(agent_workspace),
                            "source": "exec",
                            "originator": "codex_exec",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            for db in (real_home / "state_5.sqlite", isolated_home / "state_5.sqlite"):
                conn = sqlite3.connect(db)
                try:
                    conn.execute(
                        """
                        CREATE TABLE threads (
                            id TEXT PRIMARY KEY,
                            cwd TEXT NOT NULL,
                            rollout_path TEXT NOT NULL,
                            source TEXT NOT NULL,
                            thread_source TEXT,
                            updated_at INTEGER NOT NULL,
                            updated_at_ms INTEGER NOT NULL,
                            recency_at INTEGER NOT NULL,
                            recency_at_ms INTEGER NOT NULL,
                            archived INTEGER NOT NULL,
                            title TEXT NOT NULL
                        )
                        """
                    )
                    conn.commit()
                finally:
                    conn.close()

            conn = sqlite3.connect(isolated_home / "state_5.sqlite")
            try:
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        str(agent_workspace),
                        str(isolated_rollout),
                        "exec",
                        None,
                        20,
                        20000,
                        1,
                        1000,
                        0,
                        "Isolated title",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            promotion = import_codex_session_to_workspace(real_home, isolated_home, session_id, workspace)

            self.assertTrue(promotion.state_found)
            self.assertTrue(promotion.state_updated)
            self.assertTrue(promotion.rollout_found)
            self.assertTrue(promotion.rollout_updated)
            self.assertTrue(promotion.source_promoted)

            imported_rollout = real_home / "sessions" / "2026" / "07" / "07" / isolated_rollout.name
            self.assertTrue(imported_rollout.exists())

            conn = sqlite3.connect(real_home / "state_5.sqlite")
            try:
                row = conn.execute(
                    "SELECT cwd, rollout_path, source, thread_source, recency_at, recency_at_ms, title FROM threads WHERE id = ?",
                    (session_id,),
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row, (str(workspace.resolve()), str(imported_rollout.resolve()), "cli", "user", 20, 20000, "Isolated title"))
            meta = json.loads(imported_rollout.read_text(encoding="utf-8").splitlines()[0])["payload"]
            self.assertEqual(meta["cwd"], str(workspace.resolve()))
            self.assertEqual(meta["source"], "cli")
            self.assertEqual(meta["originator"], "codex-tui")


if __name__ == "__main__":
    unittest.main()
