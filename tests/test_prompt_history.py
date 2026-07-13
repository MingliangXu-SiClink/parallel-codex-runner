import asyncio
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import parallel_codex_runner_core.tui_textual as tui_textual
from parallel_codex_runner_core.app import parse_args
from parallel_codex_runner_core.models import AgentResult
from parallel_codex_runner_core.prompt_history import (
    PromptHistoryNavigator,
    PromptHistoryStore,
)


class PromptHistoryStoreTests(unittest.TestCase):
    def test_store_persists_history_by_workspace_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "state" / "prompt_history.json"
            workspace_a = root / "workspace-a"
            workspace_b = root / "workspace-b"
            workspace_a.mkdir()
            workspace_b.mkdir()
            store = PromptHistoryStore(path)

            self.assertTrue(store.append(workspace_a, "session-a", "first\nrequest"))
            self.assertTrue(store.append(workspace_a, "session-a", "第二个需求"))
            self.assertTrue(store.append(workspace_a, "session-a", "第二个需求"))
            self.assertTrue(store.append(workspace_a, "session-b", "other session"))
            self.assertTrue(store.append(workspace_b, "session-a", "other workspace"))

            reloaded = PromptHistoryStore(path)
            self.assertEqual(
                reloaded.entries(workspace_a, "session-a"),
                ["first\nrequest", "第二个需求", "第二个需求"],
            )
            self.assertEqual(reloaded.entries(workspace_a, "session-b"), ["other session"])
            self.assertEqual(reloaded.entries(workspace_b, "session-a"), ["other workspace"])
            self.assertEqual(reloaded.entries(workspace_a, ""), [])

    def test_store_recovers_from_malformed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "prompt_history.json"
            path.write_text("not json", encoding="utf-8")
            store = PromptHistoryStore(path)

            self.assertEqual(store.entries(root, "session"), [])
            self.assertTrue(store.append(root, "session", "recovered"))
            self.assertEqual(store.entries(root, "session"), ["recovered"])


class PromptHistoryNavigatorTests(unittest.TestCase):
    def test_navigation_preserves_and_restores_newest_draft(self) -> None:
        navigator = PromptHistoryNavigator(["first", "second"], "draft")

        self.assertEqual(navigator.navigate(-1, "draft"), "second")
        self.assertEqual(navigator.navigate(-1, "second"), "first")
        self.assertEqual(navigator.navigate(1, "first"), "second")
        self.assertEqual(navigator.navigate(1, "second"), "draft")

    def test_editing_recalled_history_promotes_it_to_the_draft_slot(self) -> None:
        navigator = PromptHistoryNavigator(["first", "second"])

        self.assertEqual(navigator.navigate(-1, ""), "second")
        navigator.note_edit("second, modified")
        self.assertEqual(navigator.navigate(-1, "second, modified"), "second")
        self.assertEqual(navigator.navigate(1, "second"), "second, modified")


@unittest.skipIf(
    getattr(tui_textual, "PcrTextualApp", None) is None,
    "textual is not installed",
)
class PromptHistoryTuiTests(unittest.TestCase):
    def test_starting_a_task_records_the_active_workspace_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "prompt_history.json"
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            args = parse_args(
                [
                    "--workspace",
                    str(workspace),
                    "--resume-session-id",
                    "session-a",
                ]
            )

            with mock.patch.dict(
                os.environ,
                {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
            ):
                app = tui_textual.PcrTextualApp(args)
                with mock.patch.object(tui_textual.threading, "Thread") as thread:
                    self.assertTrue(app._start_run("implement feature", record_history=True))
                    thread.return_value.start.assert_called_once()

            self.assertEqual(
                PromptHistoryStore(history_path).entries(workspace, "session-a"),
                ["implement feature"],
            )
            self.assertEqual(PromptHistoryStore(history_path).entries(workspace, ""), [])

    def test_finalizing_a_fresh_run_associates_prompt_with_selected_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "prompt_history.json"
            workspace = root / "workspace"
            candidate = root / "run" / "workspaces" / "agent_001"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            PromptHistoryStore(history_path).append(workspace, "", "initial request")
            args = parse_args(["--workspace", str(workspace)])

            with mock.patch.dict(
                os.environ,
                {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
            ):
                app = tui_textual.PcrTextualApp(args)
                app.pending_run_root = root / "run"
                app.pending_workspaces_root = candidate.parent
                app.pending_workspace = workspace
                app.pending_prompt = "initial request"
                app.pending_prompt_records_history = True
                app.agents[1].result = asdict(
                    AgentResult(
                        idx=1,
                        workspace_dir=str(candidate),
                        meta_dir="",
                        codex_home="",
                        stdout_log="",
                        stderr_log="",
                        final_message="",
                        command=[],
                        returncode=0,
                        status="success",
                        seconds=1.0,
                        codex_thread_id="selected-session",
                    )
                )
                with mock.patch.object(tui_textual, "sync_best_workspace_back"):
                    with mock.patch.object(
                        tui_textual,
                        "promote_best_codex_session_to_workspace",
                        return_value=None,
                    ):
                        with mock.patch.object(tui_textual, "cleanup_workspace_copies"):
                            self.assertTrue(
                                app._finalize_agent(1, require_resume=False)
                            )

            self.assertEqual(
                PromptHistoryStore(history_path).entries(
                    workspace,
                    "selected-session",
                ),
                ["initial request"],
            )

    def test_running_follow_up_defers_history_until_it_actually_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "prompt_history.json"
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            args = parse_args(["--workspace", str(workspace)])

            with mock.patch.dict(
                os.environ,
                {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
            ):
                app = tui_textual.PcrTextualApp(args)
                app._sync = lambda: None
                app.running = True
                app.agents[1].result = {"status": "success"}

                self.assertTrue(app._submit_task_prompt("follow up"))
                self.assertEqual(
                    PromptHistoryStore(history_path).entries(workspace, ""),
                    [],
                )
                self.assertTrue(app.queued_prompt_records_history)

                app.running = False
                with mock.patch.object(app, "_finalize_agent", return_value=True):
                    with mock.patch.object(
                        app,
                        "_request_run_with_storage_check",
                        return_value=True,
                    ) as start:
                        app._continue_queued_prompt()

                start.assert_called_once_with("follow up", record_history=True)

    def test_prompt_up_down_navigation_and_edited_draft(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                history_path = Path(tmp) / "prompt_history.json"
                workspace = Path(tmp) / "workspace"
                workspace.mkdir()
                store = PromptHistoryStore(history_path)
                store.append(workspace, "", "first")
                store.append(workspace, "", "second")
                args = parse_args(["--workspace", str(workspace)])

                with mock.patch.dict(
                    os.environ,
                    {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
                ):
                    app = tui_textual.PcrTextualApp(args)
                    with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                        async with app.run_test() as pilot:
                            prompt = app.query_one("#prompt")
                            prompt.focus()

                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "second")
                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "first")
                            await pilot.press("down")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "second")
                            await pilot.press("!")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "second!")
                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "second")
                            await pilot.press("down")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "second!")

        asyncio.run(run())

    def test_session_switch_changes_history_and_restores_each_draft(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                history_path = Path(tmp) / "prompt_history.json"
                workspace = Path(tmp) / "workspace"
                workspace.mkdir()
                store = PromptHistoryStore(history_path)
                store.append(workspace, "", "new-session history")
                store.append(workspace, "session-a", "resumed history")
                args = parse_args(["--workspace", str(workspace)])

                with mock.patch.dict(
                    os.environ,
                    {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
                ):
                    app = tui_textual.PcrTextualApp(args)
                    with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                        async with app.run_test() as pilot:
                            prompt = app.query_one("#prompt")
                            prompt.text = "new-session draft"
                            await pilot.pause()

                            app.resume_session_id = "session-a"
                            app._load_prompt_history_context()
                            await pilot.pause()
                            self.assertEqual(prompt.text, "")
                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "resumed history")
                            await pilot.press("!")
                            await pilot.pause()

                            app.resume_session_id = ""
                            app._load_prompt_history_context()
                            await pilot.pause()
                            self.assertEqual(prompt.text, "new-session draft")

                            app.resume_session_id = "session-a"
                            app._load_prompt_history_context()
                            await pilot.pause()
                            self.assertEqual(prompt.text, "resumed history!")

        asyncio.run(run())

    def test_multiline_prompt_uses_history_only_at_vertical_boundaries(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                history_path = Path(tmp) / "prompt_history.json"
                workspace = Path(tmp) / "workspace"
                workspace.mkdir()
                PromptHistoryStore(history_path).append(workspace, "", "older prompt")
                args = parse_args(["--workspace", str(workspace)])

                with mock.patch.dict(
                    os.environ,
                    {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
                ):
                    app = tui_textual.PcrTextualApp(args)
                    with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                        async with app.run_test() as pilot:
                            prompt = app.query_one("#prompt")
                            prompt.text = "first line\nsecond line"
                            prompt.cursor_location = (1, 11)
                            prompt.focus()
                            await pilot.pause()

                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "first line\nsecond line")
                            self.assertEqual(prompt.cursor_location[0], 0)
                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "older prompt")

        asyncio.run(run())

    def test_successful_task_submission_is_persisted_and_recallable(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                history_path = Path(tmp) / "prompt_history.json"
                workspace = Path(tmp) / "workspace"
                workspace.mkdir()
                args = parse_args(["--workspace", str(workspace)])

                with mock.patch.dict(
                    os.environ,
                    {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
                ):
                    app = tui_textual.PcrTextualApp(args)
                    with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                        async with app.run_test() as pilot:
                            prompt = app.query_one("#prompt")
                            prompt.focus()

                            def start_run(text: str, record_history: bool = False) -> bool:
                                if record_history:
                                    app._record_started_prompt(text)
                                return True

                            with mock.patch.object(app, "_start_run", side_effect=start_run):
                                await pilot.press(*"new request")
                                await pilot.press("enter")
                                await pilot.pause()

                            self.assertEqual(prompt.text, "")
                            self.assertEqual(
                                PromptHistoryStore(history_path).entries(workspace, ""),
                                ["new request"],
                            )
                            await pilot.press("up")
                            await pilot.pause()
                            self.assertEqual(prompt.text, "new request")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
