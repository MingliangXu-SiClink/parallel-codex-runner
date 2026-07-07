import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

import parallel_codex_runner_core.tui_textual as tui_textual
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
    load_resume_sessions_from_state,
    parse_args,
    prepare_agent_codex_home,
    promote_codex_session_to_workspace,
    run_one_agent,
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


class ArgParseTests(unittest.TestCase):
    def test_default_num_agents_is_five(self) -> None:
        args = parse_args(["fix tests"])

        self.assertEqual(args.num_agents, 5)


class TuiCommandTests(unittest.TestCase):
    def test_command_suggestions_only_for_slash_commands(self) -> None:
        self.assertEqual(command_suggestions("hello"), [])
        self.assertIn("/resume 1", "\n".join(command_suggestions("/resume")))

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

    def test_display_line_keeps_reasoning_and_output_separate(self) -> None:
        self.assertEqual(
            display_line_parts_from_output('{"type":"agent_reasoning","text":"thinking"}'),
            ("thought", "thinking"),
        )
        self.assertEqual(
            display_line_parts_from_output('{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}'),
            ("output", "answer"),
        )

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

            with mock.patch.object(tui_textual, "promote_best_codex_session_to_workspace") as promote:
                with mock.patch.object(tui_textual, "sync_best_workspace_back") as sync_back:
                    with mock.patch.object(tui_textual, "cleanup_workspace_copies") as cleanup:
                        promote.side_effect = lambda result, _workspace: result

                        self.assertTrue(app._finalize_agent(2))

            self.assertEqual(app.resume_session_id, "session-2")
            sync_back.assert_called_once_with(candidate, workspace.resolve())
            cleanup.assert_called_once_with(workspace.resolve(), workspaces_root)


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


class AgentCancelTests(unittest.TestCase):
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

            prepare_agent_codex_home(real_home, agent_home, agent_workspace, None)

            self.assertFalse((agent_home / "config.toml").is_symlink())
            self.assertFalse((agent_home / "profiles").is_symlink())
            self.assertFalse((agent_home / "profiles" / "default.toml").is_symlink())
            self.assertEqual((agent_home / "config.toml").read_text(encoding="utf-8"), "approval_policy = 'never'\n")
            self.assertEqual((agent_home / "profiles" / "default.toml").read_text(encoding="utf-8"), "model = 'gpt-5'\n")
            self.assertFalse((agent_home / "history.jsonl").exists())
            self.assertFalse((agent_home / "sessions").exists())

            (agent_home / "config.toml").write_text("changed\n", encoding="utf-8")
            self.assertEqual((real_home / "config.toml").read_text(encoding="utf-8"), "approval_policy = 'never'\n")

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
