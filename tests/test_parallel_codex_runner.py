import argparse
import asyncio
import io
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from unittest import mock
from pathlib import Path

import parallel_codex_runner_core.tui_textual as tui_textual
import parallel_codex_runner_core.app as app_core
import parallel_codex_runner_core.codex_cli as codex_cli_core
import parallel_codex_runner_core.workspace as workspace_core
from parallel_codex_runner_core.codex_models import CodexModelInfo, CodexModelRegistry
from parallel_codex_runner_core.diffing import build_workspace_diff_text
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
    remove_agent_codex_homes,
    run_one_agent,
    scrub_codex_home_support_entries,
    stream_to_log,
    sync_back_with_python,
)
from parallel_codex_runner_core.tui_textual import command_suggestions, display_line_from_output, display_line_parts_from_output


def make_agent_result_data(
    idx: int,
    workspace: Path,
    *,
    status: str = "success",
    seconds: float = 1.0,
    reasoning_tokens: int | None = 10,
    role: str = "candidate",
) -> dict[str, object]:
    return asdict(
        AgentResult(
            idx=idx,
            workspace_dir=str(workspace),
            meta_dir=str(workspace.parent / f"meta_{idx}"),
            codex_home=str(workspace.parent / f"codex_home_{idx}"),
            stdout_log="",
            stderr_log="",
            final_message="",
            command=[],
            returncode=0 if status == "success" else 1,
            status=status,
            seconds=seconds,
            role=role,
            codex_thread_id=f"session-{idx}" if status == "success" else None,
            reasoning_tokens=reasoning_tokens,
        )
    )


def make_config_read_process(
    developer_instructions: object = None,
    *,
    config_error: object = None,
    malformed: bool = False,
) -> mock.Mock:
    process = mock.Mock()
    process.stdin = mock.Mock()
    lines = [
        json.dumps({"method": "server/notification", "params": {}}),
        json.dumps({"id": 0, "result": {"userAgent": "codex-test"}}),
    ]
    if malformed:
        lines.append("not-json")
    elif config_error is not None:
        lines.append(json.dumps({"id": 1, "error": config_error}))
    else:
        lines.append(
            json.dumps(
                {
                    "id": 1,
                    "result": {
                        "config": {
                            "developer_instructions": developer_instructions,
                        }
                    },
                }
            )
        )
    process.stdout = io.StringIO("\n".join(lines) + "\n")
    process.stderr = io.StringIO("")
    process.poll.return_value = None
    process.wait.return_value = 0
    return process


class WorkspaceDiffTests(unittest.TestCase):
    def test_workspace_diff_shows_added_modified_deleted_and_full_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            (baseline / "modified.txt").write_text("before\nsecond\n", encoding="utf-8")
            (candidate / "modified.txt").write_text("after\nsecond\nthird\n", encoding="utf-8")
            (baseline / "deleted.txt").write_text("removed\n", encoding="utf-8")
            (candidate / "added.txt").write_text("新增内容\n", encoding="utf-8")
            (baseline / "deleted-empty.txt").touch()
            (candidate / "added-empty.txt").touch()
            (baseline / "no-newline.txt").write_text("before", encoding="utf-8")
            (candidate / "no-newline.txt").write_text("after", encoding="utf-8")
            (baseline / ".git").mkdir()
            (candidate / ".git").write_text("gitdir: ignored", encoding="utf-8")

            diff = build_workspace_diff_text(baseline, candidate)

        self.assertIn("A  added.txt", diff)
        self.assertIn("A  added-empty.txt", diff)
        self.assertIn("D  deleted.txt", diff)
        self.assertIn("D  deleted-empty.txt", diff)
        self.assertIn("M  modified.txt", diff)
        self.assertIn("new file mode 100644", diff)
        self.assertIn("deleted file mode 100644", diff)
        self.assertIn("-before\n\\ No newline at end of file", diff)
        self.assertIn("+after\n\\ No newline at end of file", diff)
        self.assertIn("+新增内容", diff)
        self.assertIn("--- /dev/null\n+++ b/added.txt", diff)
        self.assertIn("--- a/deleted.txt\n+++ /dev/null", diff)
        self.assertIn("-removed", diff)
        self.assertIn("-before", diff)
        self.assertIn("+after", diff)
        self.assertIn("+third", diff)
        self.assertNotIn("gitdir", diff)



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
    @staticmethod
    def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(workspace), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _init_git_workspace(self, workspace: Path, files: dict[str, str]) -> None:
        workspace.mkdir()
        self._git(workspace, "-c", "init.defaultBranch=main", "init")
        self._git(workspace, "config", "user.name", "Test User")
        self._git(workspace, "config", "user.email", "test@example.com")
        for name, content in files.items():
            (workspace / name).write_text(content, encoding="utf-8")
        self._git(workspace, "add", ".")
        self._git(workspace, "commit", "-m", "initial")

    def test_git_index_lock_waits_for_transient_contention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_index = root / "source-index"
            destination_index = root / "index"
            lock_path = root / "index.lock"
            source_index.write_bytes(b"replacement index")
            lock_path.write_bytes(b"external Git operation")

            def release_lock() -> None:
                time.sleep(0.1)
                lock_path.unlink()

            release = threading.Thread(target=release_lock)
            release.start()
            started = time.monotonic()
            try:
                with workspace_core._locked_git_index(
                    source_index,
                    destination_index,
                    timeout=1.0,
                    poll_interval=0.01,
                ) as acquired:
                    self.assertEqual(acquired, lock_path)
                    self.assertEqual(lock_path.read_bytes(), b"replacement index")
                    self.assertTrue(
                        workspace_core._index_lock_owner_path(lock_path).is_file()
                    )
                    os.replace(acquired, destination_index)
            finally:
                release.join()

            self.assertGreaterEqual(time.monotonic() - started, 0.08)
            self.assertEqual(destination_index.read_bytes(), b"replacement index")
            self.assertFalse(
                workspace_core._index_lock_owner_path(lock_path).exists()
            )

    def test_git_index_lock_times_out_without_deleting_external_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_index = root / "source-index"
            destination_index = root / "index"
            lock_path = root / "index.lock"
            source_index.write_bytes(b"replacement index")
            lock_path.write_bytes(b"external Git operation")

            with self.assertRaisesRegex(RuntimeError, "remained locked for 0.1s"):
                with workspace_core._locked_git_index(
                    source_index,
                    destination_index,
                    timeout=0.05,
                    poll_interval=0.01,
                ):
                    self.fail("external lock should not be acquired")

            self.assertEqual(lock_path.read_bytes(), b"external Git operation")

    def test_git_index_lock_recovers_stale_pcr_owned_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_index = root / "source-index"
            destination_index = root / "index"
            lock_path = root / "index.lock"
            owner_path = workspace_core._index_lock_owner_path(lock_path)
            source_index.write_bytes(b"replacement index")
            lock_path.write_bytes(b"abandoned PCR index")
            device, inode, ctime_ns = workspace_core._lock_identity(lock_path.stat())
            owner_path.write_text(
                json.dumps(
                    {
                        "pid": 99999999,
                        "device": device,
                        "inode": inode,
                        "ctime_ns": ctime_ns,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(workspace_core, "_process_is_alive", return_value=False):
                with workspace_core._locked_git_index(
                    source_index,
                    destination_index,
                    timeout=0.1,
                    poll_interval=0.01,
                ) as acquired:
                    self.assertEqual(acquired.read_bytes(), b"replacement index")
                    os.replace(acquired, destination_index)

            self.assertEqual(destination_index.read_bytes(), b"replacement index")
            self.assertFalse(owner_path.exists())

    def test_git_index_lock_does_not_remove_lock_with_mismatched_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_index = root / "source-index"
            destination_index = root / "index"
            lock_path = root / "index.lock"
            owner_path = workspace_core._index_lock_owner_path(lock_path)
            source_index.write_bytes(b"replacement index")
            lock_path.write_bytes(b"external Git operation")
            device, inode, ctime_ns = workspace_core._lock_identity(lock_path.stat())
            owner_path.write_text(
                json.dumps(
                    {
                        "pid": 99999999,
                        "device": device,
                        "inode": inode + 1,
                        "ctime_ns": ctime_ns,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(workspace_core, "_process_is_alive", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "remained locked"):
                    with workspace_core._locked_git_index(
                        source_index,
                        destination_index,
                        timeout=0.02,
                        poll_interval=0.005,
                    ):
                        self.fail("mismatched lock ownership must not be trusted")

            self.assertEqual(lock_path.read_bytes(), b"external Git operation")
            self.assertTrue(owner_path.exists())

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_workspace_copy_uses_worktree_and_preserves_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            self._init_git_workspace(
                workspace,
                {"keep.txt": "clean", "delete.txt": "delete me"},
            )
            self._git(workspace, "update-index", "--split-index")

            (workspace / "keep.txt").write_text("staged", encoding="utf-8")
            self._git(workspace, "add", "keep.txt")
            (workspace / "keep.txt").write_text("dirty", encoding="utf-8")
            (workspace / "delete.txt").unlink()
            (workspace / "untracked.txt").write_text("untracked", encoding="utf-8")
            original_status = self._git(workspace, "status", "--porcelain=v1").stdout

            run_base = root / "runs"
            dst = run_base / "workspaces" / "agent_001"
            copy_workspace(workspace, dst, run_base)
            try:
                self.assertTrue((dst / ".git").is_file())
                self.assertEqual((dst / "keep.txt").read_text(encoding="utf-8"), "dirty")
                self.assertFalse((dst / "delete.txt").exists())
                self.assertEqual((dst / "untracked.txt").read_text(encoding="utf-8"), "untracked")
                copied_status = self._git(dst, "status", "--porcelain=v1").stdout
                self.assertEqual(copied_status, original_status)
            finally:
                cleanup_workspace_copy(workspace, dst)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_workspace_copy_holds_index_lock_while_syncing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            self._init_git_workspace(workspace, {"tracked.txt": "base"})
            (workspace / "tracked.txt").write_text("dirty", encoding="utf-8")
            run_base = root / "runs"
            dst = run_base / "workspaces" / "agent_001"
            original_sync = workspace_core.sync_back_with_python
            observed_lock = []

            def sync_with_lock_check(src: Path, destination: Path) -> None:
                destination_index = workspace_core._git_index_path(destination)
                self.assertIsNotNone(destination_index)
                assert destination_index is not None
                lock_path = destination_index.with_name(f"{destination_index.name}.lock")
                observed_lock.append(lock_path.is_file())
                self.assertTrue(
                    workspace_core._index_lock_owner_path(lock_path).is_file()
                )
                original_sync(src, destination)

            with mock.patch.object(
                workspace_core,
                "sync_back_with_python",
                side_effect=sync_with_lock_check,
            ):
                copy_workspace(workspace, dst, run_base)
            try:
                self.assertEqual(observed_lock, [True])
                self.assertEqual((dst / "tracked.txt").read_text(encoding="utf-8"), "dirty")
            finally:
                cleanup_workspace_copy(workspace, dst)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_sync_moves_branch_and_preserves_index_and_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            self._init_git_workspace(
                workspace,
                {"tracked.txt": "base", "delete.txt": "base"},
            )

            run_base = root / "runs"
            agent = run_base / "workspaces" / "agent_001"
            copy_workspace(workspace, agent, run_base)
            try:
                (agent / "tracked.txt").write_text("committed", encoding="utf-8")
                (agent / "committed.txt").write_text("committed", encoding="utf-8")
                self._git(agent, "add", "-A")
                self._git(agent, "commit", "-m", "agent commit")
                self._git(agent, "update-index", "--split-index")

                (agent / "committed.txt").write_text("unstaged after commit", encoding="utf-8")
                (agent / "delete.txt").unlink()
                (agent / "staged.txt").write_text("staged after commit", encoding="utf-8")
                self._git(agent, "add", "staged.txt")
                agent_head = self._git(agent, "rev-parse", "HEAD").stdout.strip()
                agent_status = self._git(agent, "status", "--porcelain=v1").stdout

                workspace_core.sync_best_workspace_back(agent, workspace)

                self.assertEqual(
                    self._git(workspace, "rev-parse", "HEAD").stdout.strip(),
                    agent_head,
                )
                self.assertEqual(
                    self._git(workspace, "branch", "--show-current").stdout.strip(),
                    "main",
                )
                self.assertEqual(
                    self._git(workspace, "status", "--porcelain=v1").stdout,
                    agent_status,
                )
                self.assertEqual((workspace / "committed.txt").read_text(encoding="utf-8"), "unstaged after commit")
                self.assertFalse((workspace / "delete.txt").exists())

                workspace_core.sync_best_workspace_back(agent, workspace)
                self.assertEqual(
                    self._git(workspace, "status", "--porcelain=v1").stdout,
                    agent_status,
                )
            finally:
                cleanup_workspace_copy(workspace, agent)

            self.assertFalse(agent.exists())
            self.assertEqual(self._git(workspace, "rev-parse", "HEAD").stdout.strip(), agent_head)
            self._git(workspace, "cat-file", "-e", f"{agent_head}^{{commit}}")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_sync_rejects_original_head_change_during_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            self._init_git_workspace(workspace, {"tracked.txt": "base"})

            run_base = root / "runs"
            agent = run_base / "workspaces" / "agent_001"
            copy_workspace(workspace, agent, run_base)
            try:
                (agent / "tracked.txt").write_text("agent", encoding="utf-8")
                self._git(agent, "add", ".")
                self._git(agent, "commit", "-m", "agent commit")

                (workspace / "original.txt").write_text("original", encoding="utf-8")
                self._git(workspace, "add", ".")
                self._git(workspace, "commit", "-m", "original commit")
                original_head = self._git(workspace, "rev-parse", "HEAD").stdout.strip()

                with self.assertRaisesRegex(RuntimeError, "original Git HEAD changed"):
                    workspace_core.sync_best_workspace_back(agent, workspace)

                self.assertEqual(
                    self._git(workspace, "rev-parse", "HEAD").stdout.strip(),
                    original_head,
                )
                self.assertEqual((workspace / "tracked.txt").read_text(encoding="utf-8"), "base")
                self.assertEqual((workspace / "original.txt").read_text(encoding="utf-8"), "original")
            finally:
                cleanup_workspace_copy(workspace, agent)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_sync_rejects_original_branch_change_during_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            self._init_git_workspace(workspace, {"tracked.txt": "base"})

            run_base = root / "runs"
            agent = run_base / "workspaces" / "agent_001"
            copy_workspace(workspace, agent, run_base)
            try:
                self._git(workspace, "switch", "-c", "other")

                with self.assertRaisesRegex(RuntimeError, "original Git branch changed"):
                    workspace_core.sync_best_workspace_back(agent, workspace)

                self.assertEqual(self._git(workspace, "branch", "--show-current").stdout.strip(), "other")
            finally:
                cleanup_workspace_copy(workspace, agent)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_sync_recovers_commit_from_legacy_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agent = root / "agent"
            self._init_git_workspace(workspace, {"tracked.txt": "base"})
            self._git(workspace, "worktree", "add", "--detach", str(agent), "HEAD")
            try:
                (agent / "tracked.txt").write_text("legacy agent", encoding="utf-8")
                self._git(agent, "add", ".")
                self._git(agent, "commit", "-m", "legacy agent commit")
                agent_head = self._git(agent, "rev-parse", "HEAD").stdout.strip()

                workspace_core.sync_best_workspace_back(agent, workspace)

                self.assertEqual(
                    self._git(workspace, "rev-parse", "HEAD").stdout.strip(),
                    agent_head,
                )
            finally:
                cleanup_workspace_copy(workspace, agent)

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


class RunStorageEstimateTests(unittest.TestCase):
    def test_workspace_estimate_matches_copy_exclusions_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "source.bin").write_bytes(b"x" * 8192)
            (workspace / ".codex_parallel_runs").mkdir()
            (workspace / ".codex_parallel_runs" / "old.bin").write_bytes(
                b"x" * (2 * 1024 * 1024)
            )
            external = root / "external.bin"
            external.write_bytes(b"x" * (2 * 1024 * 1024))
            (workspace / "linked.bin").symlink_to(external)

            with mock.patch.object(
                workspace_core,
                "git_workspace_toplevel",
                return_value=None,
            ):
                estimate = workspace_core.estimate_workspace_copy_bytes(workspace)

        self.assertGreaterEqual(estimate, 8192)
        self.assertLess(estimate, 1024 * 1024)

    def test_agent_metadata_estimate_includes_resume_rollout_and_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            state = codex_home / "state_5.sqlite"
            wal = codex_home / "state_5.sqlite-wal"
            rollout = codex_home / "sessions" / "rollout-session-1.jsonl"
            config.write_bytes(b"c" * 1024)
            state.write_bytes(b"s" * 4096)
            wal.write_bytes(b"w" * 2048)
            rollout.parent.mkdir()
            rollout.write_bytes(b"r" * 8192)

            estimated = app_core.estimate_agent_metadata_bytes(
                codex_home,
                "session-1",
                runtime_reserve_bytes=123,
            )
            expected = 123 + sum(
                workspace_core.estimate_path_storage_bytes(path)
                for path in (config, state, wal, rollout)
            )

        self.assertEqual(estimated, expected)

    def test_run_storage_estimate_multiplies_workspace_and_meta_per_agent(self) -> None:
        with mock.patch.object(
            app_core,
            "estimate_workspace_copy_bytes",
            return_value=100,
        ), mock.patch.object(
            app_core,
            "estimate_agent_metadata_bytes",
            return_value=50,
        ):
            estimate = app_core.estimate_run_storage(Path.cwd(), 3)

        self.assertEqual(estimate.workspace_bytes_per_agent, 100)
        self.assertEqual(estimate.workspace_copies_bytes, 300)
        self.assertEqual(estimate.metadata_bytes_per_agent, 50)
        self.assertEqual(estimate.metadata_bytes, 150)
        self.assertEqual(estimate.total_bytes, 450)

    def test_staged_storage_estimate_uses_resume_for_every_agent(self) -> None:
        with mock.patch.object(
            app_core,
            "estimate_workspace_copy_bytes",
            return_value=100,
        ), mock.patch.object(
            app_core,
            "estimate_agent_metadata_bytes",
            return_value=80,
        ) as estimate_metadata:
            estimate = app_core.estimate_staged_run_storage(
                Path.cwd(),
                candidate_agents=3,
                synthesis_agents=2,
                resume_session_id="session-1",
            )

        self.assertEqual(estimate.num_agents, 5)
        self.assertEqual(estimate.workspace_copies_bytes, 500)
        self.assertEqual(estimate.metadata_bytes, 400)
        self.assertEqual(estimate.total_bytes, 900)
        estimate_metadata.assert_called_once()
        self.assertEqual(estimate_metadata.call_args.args[1], "session-1")


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

    def test_effort_uses_codex_config_override(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  -c, --config <key=value>\n",
            Path("final.md"),
            effort="xhigh",
        )

        self.assertTrue(caps["effort"])
        self.assertIn("--config", cmd)
        self.assertIn('model_reasoning_effort="xhigh"', cmd)

    def test_codex_config_accepts_one_second_multi_agent_waits(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  -c, --config <key=value>\n",
            Path("final.md"),
        )

        self.assertTrue(caps["multi_agent_wait_timeout"])
        self.assertIn(
            "features.multi_agent_v2.min_wait_timeout_ms=1000",
            cmd,
        )

    def test_multi_agent_wait_override_requires_config_support(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  --json\n",
            Path("final.md"),
        )

        self.assertFalse(caps["multi_agent_wait_timeout"])
        self.assertFalse(any("min_wait_timeout_ms" in value for value in cmd))

    def test_subagents_are_disabled_with_a_codex_config_override(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  -c, --config <key=value>\n",
            Path("final.md"),
            subagents=False,
            subagents_limit=8,
        )

        self.assertTrue(caps["subagents"])
        self.assertIn("features.multi_agent=false", cmd)
        self.assertFalse(any("max_threads" in value for value in cmd))
        self.assertFalse(any("min_wait_timeout_ms" in value for value in cmd))

    def test_subagents_limit_supports_codex_v1_and_v2(self) -> None:
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  -c, --config <key=value>\n",
            Path("final.md"),
            subagents=True,
            subagents_limit=8,
        )

        self.assertTrue(caps["subagents_limit"])
        self.assertIn("features.multi_agent=true", cmd)
        self.assertIn("agents.max_threads=8", cmd)
        self.assertIn(
            "features.multi_agent_v2.max_concurrent_threads_per_session=9",
            cmd,
        )
        self.assertIn(
            "features.multi_agent_v2.min_wait_timeout_ms=1000",
            cmd,
        )

    def test_enabling_subagents_requires_codex_config_support(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Agent 数量限制"):
            build_codex_command(
                "codex",
                "Usage: codex exec [OPTIONS]\n  --enable <FEATURE>\n",
                Path("final.md"),
                subagents=True,
                subagents_limit=8,
            )

    def test_developer_instructions_use_a_toml_config_override(self) -> None:
        instructions = "Review every candidate.\n保留原始用户消息。"
        cmd, caps = build_codex_command(
            "codex",
            "Usage: codex exec [OPTIONS]\n  -c, --config <key=value>\n",
            Path("final.md"),
            developer_instructions=instructions,
        )

        override = next(
            value for value in cmd if value.startswith("developer_instructions=")
        )
        self.assertTrue(caps["config"])
        self.assertTrue(caps["developer_instructions"])
        self.assertEqual(json.loads(override.split("=", 1)[1]), instructions)

    def test_developer_instructions_require_config_support(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "developer instructions"):
            build_codex_command(
                "codex",
                "Usage: codex exec [OPTIONS]\n",
                Path("final.md"),
                developer_instructions="Review every candidate.",
            )

    def test_codex_config_read_resolves_effective_developer_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            workspace = root / "workspace"
            codex_home.mkdir()
            workspace.mkdir()
            process = make_config_read_process("Project guidance.\n")

            with mock.patch.object(
                codex_cli_core.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                existing = (
                    codex_cli_core.read_effective_codex_developer_instructions(
                        "codex-custom",
                        codex_home,
                        workspace,
                    )
                )
                merged = codex_cli_core.merge_codex_developer_instructions(
                    existing,
                    "Review every candidate.",
                )

        self.assertEqual(existing, "Project guidance.\n")
        self.assertEqual(
            merged,
            "Project guidance.\n\n\nReview every candidate.",
        )
        popen.assert_called_once()
        self.assertEqual(
            popen.call_args.args[0],
            ["codex-custom", "app-server", "--stdio"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], str(workspace.resolve()))
        self.assertEqual(
            popen.call_args.kwargs["env"]["CODEX_HOME"],
            str(codex_home.resolve()),
        )
        requests = [
            json.loads(call.args[0])
            for call in process.stdin.write.call_args_list
        ]
        self.assertEqual(
            [request["method"] for request in requests],
            ["initialize", "initialized", "config/read"],
        )
        self.assertEqual(
            requests[-1]["params"],
            {"includeLayers": False, "cwd": str(workspace.resolve())},
        )
        process.stdin.close.assert_called_once()
        process.terminate.assert_called_once()

    def test_codex_config_read_accepts_no_existing_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            workspace = root / "workspace"
            codex_home.mkdir()
            workspace.mkdir()
            process = make_config_read_process(None)

            with mock.patch.object(
                codex_cli_core.subprocess,
                "Popen",
                return_value=process,
            ):
                existing = (
                    codex_cli_core.read_effective_codex_developer_instructions(
                        "codex",
                        codex_home,
                        workspace,
                    )
                )
                merged = codex_cli_core.merge_codex_developer_instructions(
                    existing,
                    "Review every candidate.",
                )

        self.assertIsNone(existing)
        self.assertEqual(merged, "Review every candidate.")

    def test_codex_config_read_failure_is_explicit_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            workspace = root / "workspace"
            codex_home.mkdir()
            workspace.mkdir()
            process = make_config_read_process(
                config_error={"code": -32601, "message": "Method not found"}
            )

            with mock.patch.object(
                codex_cli_core.subprocess,
                "Popen",
                return_value=process,
            ), self.assertRaisesRegex(RuntimeError, "config/read"):
                codex_cli_core.read_effective_codex_developer_instructions(
                    "codex",
                    codex_home,
                    workspace,
                )
        process.stdin.close.assert_called_once()
        process.terminate.assert_called_once()

    def test_codex_config_read_rejects_malformed_json_and_invalid_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            workspace = root / "workspace"
            codex_home.mkdir()
            workspace.mkdir()
            for process, message in (
                (make_config_read_process(malformed=True), "malformed JSON"),
                (make_config_read_process(["not", "text"]), "non-string"),
            ):
                with self.subTest(message=message), mock.patch.object(
                    codex_cli_core.subprocess,
                    "Popen",
                    return_value=process,
                ), self.assertRaisesRegex(RuntimeError, message):
                    codex_cli_core.read_effective_codex_developer_instructions(
                        "codex",
                        codex_home,
                        workspace,
                    )

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
    def test_default_agent_counts_are_four_candidates_and_two_synthesis(self) -> None:
        args = parse_args(["fix tests"])

        self.assertEqual(args.num_agents, 4)
        self.assertEqual(args.synthesis_agents, 2)

    def test_subagents_are_opt_in_with_a_configurable_limit(self) -> None:
        defaults = parse_args(["fix tests"])
        enabled = parse_args(
            ["fix tests", "--subagents", "--subagents-limit", "12"]
        )

        app_core.validate_args(defaults)
        app_core.validate_args(enabled)

        self.assertFalse(defaults.subagents)
        self.assertEqual(defaults.subagents_limit, 8)
        self.assertTrue(enabled.subagents)
        self.assertEqual(enabled.subagents_limit, 12)

    def test_non_positive_subagents_limit_is_rejected(self) -> None:
        args = parse_args(["fix tests", "--subagents-limit", "0"])

        with self.assertRaisesRegex(SystemExit, "subagents-limit"):
            app_core.validate_args(args)

    def test_effort_option_is_available_to_cli_runs(self) -> None:
        args = parse_args(["fix tests", "--effort", "xhigh"])

        self.assertEqual(args.effort, "xhigh")

    def test_synthesis_agents_are_configurable(self) -> None:
        args = parse_args(["fix tests", "--synthesis-agents", "3"])

        app_core.validate_args(args)

        self.assertEqual(args.synthesis_agents, 3)

    def test_negative_synthesis_agent_count_is_rejected(self) -> None:
        args = parse_args(["fix tests", "--synthesis-agents", "-1"])

        with self.assertRaisesRegex(SystemExit, "synthesis-agents"):
            app_core.validate_args(args)

    def test_recommend_by_replaces_best_by_without_an_alias(self) -> None:
        args = parse_args(["fix tests", "--recommend-by", "duration"])

        self.assertEqual(args.recommend_by, "duration")
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["fix tests", "--best-by", "duration"])


class SynthesisTests(unittest.TestCase):
    def test_recommendation_prefers_synthesis_then_falls_back_to_candidates(self) -> None:
        candidate = AgentResult(
            **make_agent_result_data(
                1,
                Path("/tmp/candidate"),
                reasoning_tokens=1000,
            )
        )
        synthesis = AgentResult(
            **make_agent_result_data(
                2,
                Path("/tmp/synthesis"),
                reasoning_tokens=10,
                role="synthesis",
            )
        )

        recommended = app_core.select_best_result(
            [candidate, synthesis],
            "reasoning_tokens",
        )
        fallback = app_core.select_best_result(
            [
                candidate,
                AgentResult(
                    **make_agent_result_data(
                        2,
                        Path("/tmp/failed-synthesis"),
                        status="failed",
                        role="synthesis",
                    )
                ),
            ],
            "reasoning_tokens",
        )

        self.assertEqual(recommended.idx, 2)
        self.assertEqual(fallback.idx, 1)

    def test_synthesis_context_references_every_successful_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            sources = [
                AgentResult(
                    **make_agent_result_data(idx, run_root / f"agent-{idx}")
                )
                for idx in (2, 1)
            ]

            context_path, instructions = app_core.create_synthesis_context(
                run_root,
                "Fix the failing tests.",
                sources,
            )

            context = context_path.read_text(encoding="utf-8")
            self.assertLess(context.index("AGENT-001"), context.index("AGENT-002"))
            self.assertIn("Fix the failing tests.", context)
            self.assertIn(str(sources[0].workspace_dir), context)
            self.assertIn(str(context_path), instructions)
            self.assertIn("read-only references", instructions)
            self.assertEqual(
                (run_root / "synthesis_instructions.txt").read_text(
                    encoding="utf-8"
                ),
                instructions,
            )
            self.assertFalse((run_root / "synthesis_prompt.txt").exists())

    def test_synthesis_result_refresh_does_not_replace_recorded_user_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            workspace = run_root / "workspace"
            workspace.mkdir()
            (run_root / "prompt.txt").write_text(
                "original user request",
                encoding="utf-8",
            )
            result = AgentResult(
                **make_agent_result_data(
                    2,
                    run_root / "agent-2",
                    role="synthesis",
                )
            )

            app_core.refresh_run_result_files(
                run_root,
                workspace,
                "unexpected replacement",
                [result],
                "reasoning_tokens",
            )

            self.assertEqual(
                (run_root / "prompt.txt").read_text(encoding="utf-8"),
                "original user request",
            )


class ReasoningEffortTests(unittest.TestCase):
    def test_auto_effort_falls_back_to_model_default_when_config_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(
                'model = "gpt-wide"\nmodel_reasoning_effort = "max"\n',
                encoding="utf-8",
            )
            (root / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-narrow",
                                "default_reasoning_level": "medium",
                                "supported_reasoning_levels": [
                                    {"effort": "low"},
                                    {"effort": "medium"},
                                    {"effort": "high"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            resolved = app_core.resolve_codex_reasoning_effort(
                "gpt-narrow",
                None,
                root,
            )

            self.assertEqual(resolved, "medium")
            with self.assertRaisesRegex(SystemExit, "not supported"):
                app_core.resolve_codex_reasoning_effort("gpt-narrow", "max", root)


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

    def test_run_once_launches_synthesis_agents_after_candidates_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runs = root / "runs"
            codex_home = root / "codex-home"
            workspace.mkdir()
            codex_home.mkdir()
            (workspace / "base.txt").write_text("baseline", encoding="utf-8")
            args = parse_args(
                [
                    "prompt",
                    "--workspace",
                    str(workspace),
                    "--runs-dir",
                    str(runs),
                    "-n",
                    "2",
                    "--synthesis-agents",
                    "2",
                    "--resume-session-id",
                    "session-base",
                    "--no-sync-back",
                    "--keep-workspaces",
                ]
            )
            args.cancel_event = threading.Event()
            command = [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "Path('received_prompt.txt').write_text(sys.stdin.read(), encoding='utf-8')"
                ),
            ]
            events: list[dict[str, object]] = []
            prepared_session_ids: list[str | None] = []
            build_calls: list[dict[str, object]] = []

            def copy_tree(source: Path, destination: Path, **_kwargs: object) -> None:
                shutil.copytree(source, destination)

            def prepare_home(
                _source: Path,
                destination: Path,
                _workspace: Path,
                session_id: str | None,
            ) -> None:
                destination.mkdir(parents=True, exist_ok=True)
                prepared_session_ids.append(session_id)

            def build_command(
                *_args: object,
                **kwargs: object,
            ) -> tuple[list[str], dict[str, bool]]:
                build_calls.append(dict(kwargs))
                return command, {}

            resume_session = app_core.ResumeSession(
                session_id="session-base",
                title="previous conversation",
                cwd=str(workspace),
                updated_at=1,
            )

            with mock.patch.object(
                app_core,
                "get_codex_home",
                return_value=codex_home,
            ), mock.patch.object(
                app_core,
                "resolve_resume_session",
                return_value=resume_session,
            ), mock.patch.object(
                app_core,
                "read_codex_exec_resume_help",
                return_value="Usage: codex exec resume\n  -c, --config <key=value>",
            ), mock.patch.object(
                app_core,
                "copy_workspace",
                side_effect=copy_tree,
            ), mock.patch.object(
                app_core,
                "prepare_agent_codex_home",
                side_effect=prepare_home,
            ), mock.patch.object(
                app_core,
                "read_effective_codex_developer_instructions",
                return_value="User guidance.",
            ), mock.patch.object(
                app_core,
                "build_codex_command",
                side_effect=build_command,
            ):
                returncode = app_core.run_once(
                    args,
                    "Fix the project.",
                    progress_callback=events.append,
                    print_output=False,
                )

            run_root = next(runs.iterdir())
            summary = json.loads(
                (run_root / "summary.json").read_text(encoding="utf-8")
            )
            results = summary["results"]

            self.assertEqual(returncode, 0)
            self.assertEqual(
                [result["role"] for result in results],
                ["candidate", "candidate", "synthesis", "synthesis"],
            )
            self.assertIn(summary["best_agent"], {"agent_003", "agent_004"})
            self.assertEqual(summary["synthesis"]["source_agents"], [1, 2])
            self.assertEqual(summary["synthesis"]["status"], "completed")
            self.assertEqual(
                summary["synthesis"]["resume_session_id"],
                "session-base",
            )
            self.assertEqual(prepared_session_ids, ["session-base"] * 4)
            self.assertTrue(
                all(call.get("resume_session_id") == "session-base" for call in build_calls)
            )
            for idx in range(1, 5):
                received_prompt = (
                    run_root
                    / "workspaces"
                    / f"agent_{idx:03d}"
                    / "received_prompt.txt"
                ).read_text(encoding="utf-8")
                self.assertEqual(received_prompt, "Fix the project.")
            instructions_path = run_root / "synthesis_instructions.txt"
            synthesis_instructions = instructions_path.read_text(encoding="utf-8")
            self.assertIn("second-stage synthesis Agent", synthesis_instructions)
            self.assertFalse((run_root / "synthesis_prompt.txt").exists())
            self.assertEqual(
                Path(summary["synthesis"]["instructions_path"]).resolve(),
                instructions_path.resolve(),
            )
            self.assertTrue(
                all(not call.get("developer_instructions") for call in build_calls[:2])
            )
            self.assertTrue(
                all(
                    str(call.get("developer_instructions") or "").startswith(
                        "User guidance.\n\n"
                    )
                    and
                    "second-stage synthesis Agent"
                    in str(call.get("developer_instructions") or "")
                    for call in build_calls[2:]
                )
            )
            synthesis_event = next(
                event for event in events if event.get("type") == "synthesis_started"
            )
            self.assertEqual(synthesis_event["prompt"], "Fix the project.")
            self.assertEqual(synthesis_event["user_prompt"], "Fix the project.")
            self.assertEqual(
                synthesis_event["developer_instructions"],
                synthesis_instructions,
            )
            self.assertEqual(
                Path(str(synthesis_event["instructions_path"])).resolve(),
                instructions_path.resolve(),
            )

    def test_run_once_synthesis_without_resume_starts_new_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runs = root / "runs"
            codex_home = root / "codex-home"
            workspace.mkdir()
            codex_home.mkdir()
            (workspace / "base.txt").write_text("baseline", encoding="utf-8")
            args = parse_args(
                [
                    "prompt",
                    "--workspace",
                    str(workspace),
                    "--runs-dir",
                    str(runs),
                    "-n",
                    "1",
                    "--synthesis-agents",
                    "1",
                    "--no-sync-back",
                    "--keep-workspaces",
                ]
            )
            command = [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "Path('received_prompt.txt').write_text(sys.stdin.read(), encoding='utf-8')"
                ),
            ]
            prepared_session_ids: list[str | None] = []
            build_calls: list[dict[str, object]] = []

            def copy_tree(source: Path, destination: Path, **_kwargs: object) -> None:
                shutil.copytree(source, destination)

            def prepare_home(
                _source: Path,
                destination: Path,
                _workspace: Path,
                session_id: str | None,
            ) -> None:
                destination.mkdir(parents=True, exist_ok=True)
                prepared_session_ids.append(session_id)

            def build_command(
                *_args: object,
                **kwargs: object,
            ) -> tuple[list[str], dict[str, bool]]:
                build_calls.append(dict(kwargs))
                return command, {}

            with mock.patch.object(
                app_core,
                "get_codex_home",
                return_value=codex_home,
            ), mock.patch.object(
                app_core,
                "read_codex_exec_help",
                return_value="Usage: codex exec\n  -c, --config <key=value>",
            ), mock.patch.object(
                app_core,
                "copy_workspace",
                side_effect=copy_tree,
            ), mock.patch.object(
                app_core,
                "prepare_agent_codex_home",
                side_effect=prepare_home,
            ), mock.patch.object(
                app_core,
                "read_effective_codex_developer_instructions",
                return_value=None,
            ), mock.patch.object(
                app_core,
                "build_codex_command",
                side_effect=build_command,
            ):
                returncode = app_core.run_once(
                    args,
                    "Original request.",
                    progress_callback=lambda _event: None,
                    print_output=False,
                )

            run_root = next(runs.iterdir())
            summary = json.loads(
                (run_root / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(returncode, 0)
            self.assertEqual(prepared_session_ids, [None, None])
            self.assertTrue(
                all(call.get("resume_session_id") is None for call in build_calls)
            )
            self.assertIsNone(summary["synthesis"]["resume_session_id"])
            for idx in (1, 2):
                received_prompt = (
                    run_root
                    / "workspaces"
                    / f"agent_{idx:03d}"
                    / "received_prompt.txt"
                ).read_text(encoding="utf-8")
                self.assertEqual(received_prompt, "Original request.")
            self.assertFalse(build_calls[0].get("developer_instructions"))
            self.assertIn(
                "second-stage synthesis Agent",
                str(build_calls[1].get("developer_instructions") or ""),
            )

    def test_config_resolution_failure_falls_back_to_first_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runs = root / "runs"
            codex_home = root / "codex-home"
            workspace.mkdir()
            codex_home.mkdir()
            (workspace / "base.txt").write_text("baseline", encoding="utf-8")
            args = parse_args(
                [
                    "prompt",
                    "--workspace",
                    str(workspace),
                    "--runs-dir",
                    str(runs),
                    "-n",
                    "1",
                    "--synthesis-agents",
                    "1",
                    "--no-sync-back",
                    "--keep-workspaces",
                ]
            )
            command = [sys.executable, "-c", "pass"]
            events: list[dict[str, object]] = []

            def copy_tree(source: Path, destination: Path, **_kwargs: object) -> None:
                shutil.copytree(source, destination)

            def prepare_home(
                _source: Path,
                destination: Path,
                _workspace: Path,
                _session_id: str | None,
            ) -> None:
                destination.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(
                app_core,
                "get_codex_home",
                return_value=codex_home,
            ), mock.patch.object(
                app_core,
                "read_codex_exec_help",
                return_value="Usage: codex exec\n  -c, --config <key=value>",
            ), mock.patch.object(
                app_core,
                "copy_workspace",
                side_effect=copy_tree,
            ), mock.patch.object(
                app_core,
                "prepare_agent_codex_home",
                side_effect=prepare_home,
            ), mock.patch.object(
                app_core,
                "read_effective_codex_developer_instructions",
                side_effect=RuntimeError("config/read unavailable"),
            ), mock.patch.object(
                app_core,
                "build_codex_command",
                return_value=(command, {}),
            ) as build_command:
                returncode = app_core.run_once(
                    args,
                    "Original request.",
                    progress_callback=events.append,
                    print_output=False,
                )

            run_root = next(runs.iterdir())
            summary = json.loads(
                (run_root / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(returncode, 0)
            self.assertEqual(summary["best_agent"], "agent_001")
            self.assertEqual(summary["synthesis"]["status"], "failed")
            self.assertIn("config/read unavailable", summary["synthesis"]["error"])
            build_command.assert_called_once()
            synthesis_finished = next(
                event for event in events if event.get("type") == "synthesis_finished"
            )
            self.assertIn("config/read unavailable", str(synthesis_finished["error"]))


class AdditionalAgentTests(unittest.TestCase):
    def test_additional_agents_use_global_indices_and_retry_fresh_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            run_root = root / "run"
            codex_home = root / "codex-home"
            workspace.mkdir()
            run_root.mkdir()
            codex_home.mkdir()
            (workspace / "base.txt").write_text("baseline", encoding="utf-8")
            args = parse_args(
                ["prompt", "--workspace", str(workspace), "--recommend-by", "duration"]
            )
            command = [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; Path('ran.txt').write_text(sys.stdin.read())",
            ]
            events: list[dict[str, object]] = []

            with mock.patch.object(app_core, "get_codex_home", return_value=codex_home):
                with mock.patch.object(app_core, "read_codex_exec_help", return_value="Usage: codex exec"):
                    with mock.patch.object(
                        app_core,
                        "build_codex_command",
                        return_value=(command, {}),
                    ):
                        results = app_core.run_additional_agents(
                            args=args,
                            prompt="current question",
                            agent_indices=[3, 4],
                            run_root=run_root,
                            workspace=workspace,
                            progress_callback=events.append,
                            cancel_event=threading.Event(),
                            agent_cancel_events={3: threading.Event(), 4: threading.Event()},
                        )

                        (run_root / "workspaces" / "agent_003" / "stale.txt").write_text(
                            "must disappear",
                            encoding="utf-8",
                        )

                        retry_results = app_core.run_additional_agents(
                            args=args,
                            prompt="current question",
                            agent_indices=[3],
                            run_root=run_root,
                            workspace=workspace,
                            retry_indices={3},
                            progress_callback=events.append,
                            cancel_event=threading.Event(),
                            agent_cancel_events={3: threading.Event()},
                        )

            self.assertEqual({result.idx for result in results}, {3, 4})
            self.assertTrue(all(result.status == "success" for result in results))
            self.assertEqual([result.idx for result in retry_results], [3])
            self.assertEqual(
                (run_root / "workspaces" / "agent_003" / "ran.txt").read_text(encoding="utf-8"),
                "current question",
            )
            self.assertFalse((run_root / "workspaces" / "agent_003" / "stale.txt").exists())
            self.assertTrue(any((run_root / "retry_history" / "agent_003").iterdir()))
            summary = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["recommend_by"], "duration")
            self.assertNotIn("best_by", summary)
            self.assertEqual([result["idx"] for result in summary["results"]], [3, 4])
            started = [event["idx"] for event in events if event.get("type") == "agent_started"]
            self.assertEqual(started, [3, 4, 3])

    def test_additional_agents_scrub_codex_support_after_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            run_root = root / "run"
            codex_home = root / "codex-home"
            workspace.mkdir()
            run_root.mkdir()
            codex_home.mkdir()
            args = parse_args(["prompt", "--workspace", str(workspace)])

            def fail_async_run(coroutine: object) -> None:
                coroutine.close()
                raise RuntimeError("execution failed")

            with mock.patch.object(app_core, "get_codex_home", return_value=codex_home):
                with mock.patch.object(app_core, "read_codex_exec_help", return_value="Usage: codex exec"):
                    with mock.patch.object(
                        app_core,
                        "build_codex_command",
                        return_value=([sys.executable, "-c", "pass"], {}),
                    ):
                        with mock.patch.object(app_core.asyncio, "run", side_effect=fail_async_run):
                            with mock.patch.object(
                                app_core,
                                "scrub_codex_home_support_entries",
                            ) as scrub:
                                with self.assertRaisesRegex(RuntimeError, "execution failed"):
                                    app_core.run_additional_agents(
                                        args=args,
                                        prompt="current question",
                                        agent_indices=[3],
                                        run_root=run_root,
                                        workspace=workspace,
                                    )

            scrub.assert_called_with(
                run_root.resolve() / "meta" / "agent_003" / "codex_home"
            )


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

        self.assertIn(("default (Codex CLI)", ""), options)
        self.assertIn(("gpt-visible", "gpt-visible"), options)
        self.assertIn(("gpt-custom", "gpt-custom"), options)
        self.assertNotIn(("gpt-hidden", "gpt-hidden"), options)

    def test_tui_effort_choices_follow_selected_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(
                'model = "gpt-wide"\nmodel_reasoning_effort = "max"\n',
                encoding="utf-8",
            )
            (root / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-wide",
                                "visibility": "list",
                                "default_reasoning_level": "medium",
                                "supported_reasoning_levels": [
                                    {"effort": "low"},
                                    {"effort": "medium"},
                                    {"effort": "max"},
                                ],
                            },
                            {
                                "slug": "gpt-narrow",
                                "visibility": "list",
                                "default_reasoning_level": "medium",
                                "supported_reasoning_levels": [
                                    {"effort": "low"},
                                    {"effort": "medium"},
                                    {"effort": "high"},
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(tui_textual, "get_codex_home", return_value=root):
                wide = tui_textual.codex_effort_options("gpt-wide", None)
                narrow = tui_textual.codex_effort_options("gpt-narrow", None)

        self.assertEqual(wide[0], ("auto (max)", ""))
        self.assertIn(("max", "max"), wide)
        self.assertEqual(narrow[0], ("auto (medium)", ""))
        self.assertNotIn(("max", "max"), narrow)

    def test_command_suggestions_only_for_slash_commands(self) -> None:
        self.assertEqual(command_suggestions("hello"), [])
        slash_commands = "\n".join(command_suggestions("/"))
        self.assertIn("/kill [agent]", "\n".join(command_suggestions("/k")))
        self.assertIn("/accept", "\n".join(command_suggestions("/a")))
        self.assertIn("/reject", "\n".join(command_suggestions("/rej")))
        self.assertIn("/retry [agent]", "\n".join(command_suggestions("/ret")))
        self.assertIn("/more <n>", "\n".join(command_suggestions("/mo")))
        self.assertIn("/diff", "\n".join(command_suggestions("/d")))
        self.assertIn("/resume <n|session>", "\n".join(command_suggestions("/resume")))
        self.assertIn("/model <name|clear>", "\n".join(command_suggestions("/model")))
        self.assertIn("/effort <auto|level>", "\n".join(command_suggestions("/effort")))
        self.assertIn(
            "/subagents <on|off>",
            "\n".join(command_suggestions("/subagents")),
        )
        self.assertIn(
            "/subagentslimit <n>",
            "\n".join(command_suggestions("/subagentsl")),
        )
        self.assertIn("/recommendby <duration|reasoning_tokens>", slash_commands)
        self.assertEqual(command_suggestions("/bestby"), [])
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
        self.assertIn(
            "queue without stopping the active run",
            tui_textual.build_help_text(),
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_tip_row_rotates_text_and_animates_icon_above_prompt(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test(size=(80, 30)) as pilot:
                    tips = app.query_one("#tips")
                    prompt = app.query_one("#prompt")
                    first_tip = app.current_tip
                    first_icon = app.current_tip_icon
                    first_icon_color = app.current_tip_icon_color

                    self.assertEqual(tui_textual.TIP_ROTATION_SECONDS, 10.0)
                    self.assertEqual(tui_textual.TIP_ICON_REFRESH_SECONDS, 0.1)
                    self.assertEqual(len(tui_textual.TIP_ICON_COLORS), 12)
                    self.assertTrue(any("/kill" in tip for tip in tui_textual.TUI_TIPS))
                    self.assertTrue(any("/accept" in tip for tip in tui_textual.TUI_TIPS))
                    self.assertTrue(any("/diff" in tip for tip in tui_textual.TUI_TIPS))
                    self.assertTrue(any("SUBAGENTS" in tip for tip in tui_textual.TUI_TIPS))
                    self.assertTrue(any("60 秒" in tip for tip in tui_textual.TUI_TIPS))
                    self.assertEqual(tui_textual.FOLLOW_UP_DELAY_SECONDS, 60.0)
                    self.assertEqual(tips.region.height, 1)
                    self.assertLess(tips.region.y, prompt.region.y)
                    self.assertIn(first_tip, tips.content.plain)
                    self.assertIn(first_icon, tips.content.plain)
                    self.assertNotIn("TIPS", tips.content.plain)

                    app._advance_tip_icon()
                    await pilot.pause()

                    self.assertEqual(app.current_tip_icon, first_icon)
                    self.assertNotEqual(app.current_tip_icon_color, first_icon_color)
                    self.assertEqual(app.current_tip, first_tip)
                    self.assertIn(app.current_tip_icon, tips.content.plain)

                    app._advance_tip()
                    await pilot.pause()

                    self.assertNotEqual(app.current_tip, first_tip)
                    self.assertIn(app.current_tip, tips.content.plain)
                    self.assertEqual(tips.region.height, 1)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_config_commands_update_cli_equivalents(self) -> None:
        args = parse_args([])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app._show_text = lambda _text: None

        app._handle_command("/numofagents 4")
        app._handle_command("/synthesis 2")
        app._handle_command("/maxparallel 2")
        app._handle_command("/subagents on")
        app._handle_command("/subagentslimit 12")
        app._handle_command("/serial")
        app._handle_command("/recommendby duration")
        app._handle_command("/model gpt-5")
        app._handle_command("/effort high")
        app._handle_command("/syncback off")
        app._handle_command("/keepworkspaces on")
        app._handle_command("/resumeinclude off")

        self.assertEqual(app.num_agents, 4)
        self.assertEqual(app.args.num_agents, 4)
        self.assertEqual(app.synthesis_agents, 2)
        self.assertEqual(app.args.synthesis_agents, 2)
        self.assertEqual(app.args.max_parallel, 2)
        self.assertTrue(app.args.subagents)
        self.assertEqual(app.args.subagents_limit, 12)
        self.assertTrue(app.args.serial)
        self.assertEqual(app.args.recommend_by, "duration")
        self.assertEqual(app.args.model, "gpt-5")
        self.assertEqual(app.args.effort, "high")
        self.assertTrue(app.args.no_sync_back)
        self.assertTrue(app.args.keep_workspaces)
        self.assertFalse(app.args.resume_include_non_interactive)

        app._handle_command("/parallel")
        app._handle_command("/maxparallel auto")
        app._handle_command("/subagents off")
        app._handle_command("/model clear")
        app._handle_command("/effort auto")
        self.assertFalse(app.args.serial)
        self.assertIsNone(app.args.max_parallel)
        self.assertFalse(app.args.subagents)
        self.assertIsNone(app.args.model)
        self.assertIsNone(app.args.effort)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_prepared_rows_keep_codex_bin_visible(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["--codex-bin", "/opt/codex/bin/codex"]))
        app._sync = lambda: None
        app._on_runner_event(
            tui_textual.RunnerEvent(
                {
                    "type": "run_prepared",
                    "rows": [
                        ["WORKSPACE", str(app.workspace)],
                        ["RUNS_ROOT", "/tmp/pcr-run"],
                        ["CODEX_BIN", "/opt/codex/bin/codex"],
                    ],
                }
            )
        )
        self.assertEqual(
            dict(app.run_info_rows)["CODEX_BIN"],
            "/opt/codex/bin/codex",
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_model_change_resets_an_unsupported_effort_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(
                'model = "gpt-wide"\nmodel_reasoning_effort = "max"\n',
                encoding="utf-8",
            )
            (root / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-wide",
                                "default_reasoning_level": "medium",
                                "supported_reasoning_levels": [
                                    {"effort": "medium"},
                                    {"effort": "max"},
                                ],
                            },
                            {
                                "slug": "gpt-narrow",
                                "default_reasoning_level": "medium",
                                "supported_reasoning_levels": [
                                    {"effort": "low"},
                                    {"effort": "medium"},
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            args = parse_args(["--model", "gpt-wide", "--effort", "max"])
            with mock.patch.object(tui_textual, "get_codex_home", return_value=root):
                app = tui_textual.PcrTextualApp(args)
            app._sync = lambda: None
            app._show_text = lambda _text: None

            app._handle_command("/model gpt-narrow")

        self.assertEqual(app.args.model, "gpt-narrow")
        self.assertIsNone(app.args.effort)
        self.assertEqual(
            app.model_registry.model_options(None)[0][0],
            "default (gpt-wide)",
        )
        self.assertEqual(dict(app._base_info_rows())["EFFORT"], "auto (medium)")
        self.assertNotIn(
            ("max", "max"),
            app.model_registry.effort_options("gpt-narrow", app.args.effort),
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_model_select_refreshes_effort_select_options(self) -> None:
        async def run() -> None:
            args = parse_args(["--model", "gpt-wide", "--effort", "max"])
            app = tui_textual.PcrTextualApp(args)
            app.model_registry = CodexModelRegistry(
                models={
                    "gpt-wide": CodexModelInfo(
                        "gpt-wide", "medium", ("medium", "max")
                    ),
                    "gpt-narrow": CodexModelInfo(
                        "gpt-narrow", "medium", ("low", "medium")
                    ),
                }
            )
            app.model_choices = app.model_registry.model_options(args.model)
            app.effort_choices = app.model_registry.effort_options(
                args.model, args.effort
            )
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    model = app.query_one("#config-model")
                    model.value = "gpt-narrow"
                    await pilot.pause()
                    effort = app.query_one("#config-effort")

                    self.assertIsNone(app.args.effort)
                    self.assertIn("medium", effort._legal_values)
                    self.assertNotIn("max", effort._legal_values)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_next_run_config_does_not_finalize_completed_selection(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "5"]))
        app._sync = lambda: None
        app._show_text = lambda _text: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_workspace = app.workspace
        app.pending_no_sync_back = False
        app.pending_keep_workspaces = False
        app.recommended_agent = 5
        app.selected_agent = 4
        completed_agents = app.agents

        with mock.patch.object(app, "_finalize_agent") as finalize:
            with mock.patch.object(app, "_discard_pending_run") as discard:
                app._handle_command("/numofagents 2")
                app._handle_command("/maxparallel 1")
                app._handle_command("/recommendby duration")
                app._handle_command("/model gpt-5")
                app._handle_command("/syncback off")
                app._handle_command("/keepworkspaces on")

        finalize.assert_not_called()
        discard.assert_not_called()
        self.assertIs(app.agents, completed_agents)
        self.assertEqual(app.selected_agent, 4)
        self.assertEqual(app.recommended_agent, 5)
        self.assertEqual(app.num_agents, 2)
        self.assertTrue(app._has_pending_run())
        self.assertTrue(app._pending_sync_disabled())
        self.assertTrue(app._pending_keep_enabled())
        self.assertTrue(app.args.no_sync_back)
        self.assertTrue(app.args.keep_workspaces)
        with mock.patch.object(tui_textual, "cleanup_workspace_copies") as cleanup:
            self.assertTrue(app._cleanup_after_pending_run())
        cleanup.assert_not_called()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_cleanup_removes_agent_codex_homes_with_workspaces(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        app.pending_run_root = Path("/tmp/pcr-test")
        app.pending_workspaces_root = app.pending_run_root / "workspaces"
        app.pending_workspace = app.workspace
        app.pending_keep_workspaces = False
        with (
            mock.patch.object(tui_textual, "cleanup_workspace_copies") as cleanup,
            mock.patch.object(tui_textual, "remove_agent_codex_homes") as remove_homes,
        ):
            self.assertTrue(app._cleanup_after_pending_run())
        cleanup.assert_called_once_with(app.workspace, app.pending_workspaces_root)
        remove_homes.assert_called_once_with(app.pending_run_root / "meta")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_exit_discards_pending_run_after_sync_is_disabled(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "5"]))
        app._sync = lambda: None
        app._show_text = lambda _text: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.recommended_agent = 5
        app.selected_agent = 3
        app.agents[3].result = {"status": "success"}
        app._handle_syncback(["off"])

        with (
            mock.patch.object(app, "_finalize_agent", return_value=True) as finalize,
            mock.patch.object(app, "_discard_pending_run", return_value=True) as discard,
            mock.patch.object(app, "exit") as exit_app,
        ):
            app._request_exit()

        finalize.assert_not_called()
        discard.assert_called_once_with()
        exit_app.assert_called_once_with()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_exit_does_not_fallback_when_displayed_agent_failed(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "5"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.recommended_agent = 5
        app.selected_agent = 3
        app.agents[3].result = {"status": "failed"}
        app.agents[5].result = {"status": "success"}

        with mock.patch.object(app, "_finalize_agent", return_value=False) as finalize:
            with mock.patch.object(app, "exit") as exit_app:
                app._request_exit()

        finalize.assert_called_once_with(
            3,
            require_resume=False,
            archive_detail=False,
        )
        exit_app.assert_not_called()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_exit_discards_run_when_no_agent_succeeded(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False

        with mock.patch.object(app, "_discard_pending_run", return_value=True) as discard:
            with mock.patch.object(app, "exit") as exit_app:
                app._request_exit()

        discard.assert_called_once_with()
        exit_app.assert_called_once_with()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_accept_finalizes_displayed_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "3"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.selected_agent = 2
        app.agents[2].result = make_agent_result_data(2, Path("/tmp/agent-2"))

        with mock.patch.object(app, "_finalize_agent", return_value=True) as finalize:
            app._handle_command("/accept")

        finalize.assert_called_once_with(2, archive_detail=True)
        self.assertEqual(app.status, "Accepted AGENT-002")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_accept_stops_active_run_before_finalizing(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "3"]))
        app._sync = lambda: None
        app.running = True
        app.cancel_event = threading.Event()
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.selected_agent = 2
        app.agents[2].result = make_agent_result_data(2, Path("/tmp/agent-2"))

        app._handle_command("/accept")

        self.assertTrue(app.cancel_event.is_set())
        self.assertEqual(app.pending_accept_agent, 2)
        with mock.patch.object(app, "_finalize_agent", return_value=True) as finalize:
            app._on_runner_event(
                tui_textual.RunnerEvent(
                    {
                        "type": "run_finished",
                        "run_root": "/tmp/pcr-test/run",
                        "cancelled": True,
                    }
                )
            )

        finalize.assert_called_once_with(2, archive_detail=True)
        self.assertIsNone(app.pending_accept_agent)
        self.assertEqual(app.status, "Accepted AGENT-002")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_accept_respects_no_sync_back(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "1", "--no-sync-back"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = True
        app.agents[1].result = make_agent_result_data(1, Path("/tmp/agent-1"))

        with mock.patch.object(app, "_finalize_agent") as finalize:
            app._handle_command("/accept")

        finalize.assert_not_called()
        self.assertEqual(app.status, "Cannot accept while sync back is disabled")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_reject_excludes_agent_from_recommendation(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_execution_args = argparse.Namespace(recommend_by="reasoning_tokens")
        app.agents[1].result = make_agent_result_data(
            1,
            Path("/tmp/agent-1"),
            reasoning_tokens=100,
        )
        app.agents[2].result = make_agent_result_data(
            2,
            Path("/tmp/agent-2"),
            reasoning_tokens=50,
        )
        app._recompute_recommendation()
        self.assertEqual(app.recommended_agent, 1)

        app.selected_agent = 1
        app._handle_command("/reject")

        self.assertTrue(app.agents[1].rejected)
        self.assertEqual(app.recommended_agent, 2)
        self.assertIn("rejected", app._detail_title(app.agents[1]))

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_prefers_synthesis_without_changing_displayed_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.pending_execution_args = argparse.Namespace(
            recommend_by="reasoning_tokens"
        )
        app.agents[1].result = make_agent_result_data(
            1,
            Path("/tmp/candidate-1"),
            reasoning_tokens=1000,
        )
        app.agents[3] = tui_textual.AgentPane(
            idx=3,
            role="synthesis",
            result=make_agent_result_data(
                3,
                Path("/tmp/synthesis-3"),
                reasoning_tokens=10,
                role="synthesis",
            ),
        )
        app.selected_agent = 1

        app._recompute_recommendation()

        self.assertEqual(app.recommended_agent, 3)
        self.assertEqual(app.selected_agent, 1)
        app.agents[3].rejected = True
        app._recompute_recommendation()
        self.assertEqual(app.recommended_agent, 1)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_creates_synthesis_panes_from_core_stage_event(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.running = True
        app.pending_prompt = "original request"

        app._on_runner_event(
            tui_textual.RunnerEvent(
                {
                    "type": "synthesis_started",
                    "indices": [3, 4],
                    "source_agents": [1, 2],
                    "prompt": "original request",
                    "user_prompt": "original request",
                    "developer_instructions": "internal synthesis instructions",
                }
            )
        )

        self.assertEqual(set(app.agents), {1, 2, 3, 4})
        self.assertEqual(app.agents[3].role, "synthesis")
        self.assertEqual(app.agents[3].input_text, "original request")
        self.assertEqual(
            app.agents[3].execution_prompt,
            "original request",
        )
        self.assertEqual(
            app.agents[3].developer_instructions,
            "internal synthesis instructions",
        )
        detail_text = "\n".join(
            text for _prefix, text, _style in app._detail_blocks(app.agents[3])
        )
        self.assertIn("original request", detail_text)
        self.assertNotIn("internal synthesis instructions", detail_text)
        self.assertIn("synthesis", app._detail_title(app.agents[3]))

        app._on_runner_event(
            tui_textual.RunnerEvent(
                {
                    "type": "synthesis_finished",
                    "indices": [3, 4],
                    "error": "copy failed",
                }
            )
        )
        self.assertEqual(app.agents[3].status, "error")
        self.assertEqual(app.agents[4].status, "error")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_follow_up_still_uses_rejected_displayed_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "1"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.recommended_agent = None
        app.selected_agent = 1
        app.agents[1].rejected = True
        app.agents[1].result = make_agent_result_data(1, Path("/tmp/agent-1"))

        with mock.patch.object(app, "_commit_runner_inputs", return_value=True):
            with mock.patch.object(app, "_discard_pending_run") as discard:
                with mock.patch.object(app, "_finalize_agent", return_value=False) as finalize:
                    self.assertFalse(app._start_run("follow up"))

        discard.assert_not_called()
        finalize.assert_called_once_with(1, archive_detail=True)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_more_preserves_auto_parallel_setting(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None

        with mock.patch.object(app, "_commit_runner_inputs", return_value=True):
            with mock.patch.object(tui_textual.threading, "Thread"):
                self.assertTrue(app._start_run("current question"))

        self.assertIsNone(app.pending_execution_args.max_parallel)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_more_does_not_reuse_reserved_synthesis_indices(self) -> None:
        app = tui_textual.PcrTextualApp(
            parse_args(["-n", "2", "--synthesis-agents", "2"])
        )
        app._sync = lambda: None

        with mock.patch.object(app, "_commit_runner_inputs", return_value=True):
            with mock.patch.object(tui_textual.threading, "Thread"):
                self.assertTrue(app._start_run("current question"))

        app._handle_command("/more 2")

        self.assertEqual(set(app.agents), {1, 2, 5, 6})
        self.assertEqual(app.candidate_batches[0].indices, [5, 6])
        self.assertTrue({3, 4}.issubset(app.agent_cancel_events))

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_retry_and_more_queue_candidates_for_current_question(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "3"]))
        app._sync = lambda: None
        app.running = True
        app.pending_prompt = "current question"
        app.pending_execution_args = argparse.Namespace(**vars(app.args))
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.agents[2].result = make_agent_result_data(
            2,
            Path("/tmp/agent-2"),
            status="killed",
        )
        app.selected_agent = 2

        app._handle_command("/retry")
        app._handle_command("/more 3")

        self.assertEqual(app.candidate_batches[0].indices, [2])
        self.assertEqual(app.candidate_batches[0].retry_indices, {2})
        self.assertEqual(app.candidate_batches[1].indices, [4, 5, 6])
        self.assertEqual(app.agents[2].status, "retry queued")
        self.assertEqual(
            [app.agents[idx].input_text for idx in (4, 5, 6)],
            ["current question"] * 3,
        )

        app.selected_agent = 6
        app._clear_pending_run()
        self.assertNotIn(4, app.agents)
        self.assertNotIn(5, app.agents)
        self.assertNotIn(6, app.agents)
        self.assertEqual(app.agents[2].status, "killed")
        self.assertEqual(app.selected_agent, 3)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_retry_preserves_synthesis_role_and_internal_instructions(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "1"]))
        app._sync = lambda: None
        app.running = True
        app.pending_prompt = "original request"
        app.pending_execution_args = argparse.Namespace(**vars(app.args))
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.agents[2] = tui_textual.AgentPane(
            idx=2,
            role="synthesis",
            status="failed",
            execution_prompt="original request",
            developer_instructions="internal synthesis instructions",
            result=make_agent_result_data(
                2,
                Path("/tmp/synthesis-2"),
                status="failed",
                role="synthesis",
            ),
        )
        app.selected_agent = 2

        app._handle_command("/retry")

        batch = app.candidate_batches[0]
        self.assertEqual(batch.role, "synthesis")
        self.assertEqual(batch.prompt, "original request")
        self.assertEqual(
            batch.developer_instructions,
            "internal synthesis instructions",
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_launches_queued_candidate_batch_with_run_start_settings(self) -> None:
        app = tui_textual.PcrTextualApp(
            parse_args(
                [
                    "-n",
                    "2",
                    "--model",
                    "original-model",
                    "--resume-session-id",
                    "run-start-session",
                ]
            )
        )
        app._sync = lambda: None
        app.pending_prompt = "current question"
        app.pending_run_root = Path("/tmp/pcr-test/run")
        app.pending_workspaces_root = app.pending_run_root / "workspaces"
        app.pending_workspace = Path("/tmp/pcr-test/workspace")
        app.pending_execution_args = argparse.Namespace(**vars(app.args))
        app.resume_session_id = "later-live-session"
        app.args.resume_session_id = "later-live-session"
        app.agents[3] = tui_textual.AgentPane(
            idx=3,
            status="queued",
            input_text="current question",
        )
        app.candidate_batches.append(tui_textual.CandidateBatch([3]))
        events: list[dict[str, object]] = []
        app._post_progress = events.append

        with mock.patch.object(tui_textual, "run_additional_agents", return_value=[]) as run_more:
            with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                self.assertTrue(app._launch_next_candidate_batch())
                thread_cls.call_args.kwargs["target"]()

        run_more.assert_called_once()
        call = run_more.call_args.kwargs
        self.assertEqual(call["agent_indices"], [3])
        self.assertEqual(call["prompt"], "current question")
        self.assertEqual(call["args"].model, "original-model")
        self.assertEqual(call["resume_session_id"], "run-start-session")
        self.assertEqual(events[-1]["type"], "candidate_batch_finished")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_synthesis_retry_inherits_resume_and_internal_instructions(self) -> None:
        app = tui_textual.PcrTextualApp(
            parse_args(["-n", "1", "--resume-session-id", "candidate-session"])
        )
        app._sync = lambda: None
        app.pending_prompt = "original request"
        app.pending_run_root = Path("/tmp/pcr-test/run")
        app.pending_workspaces_root = app.pending_run_root / "workspaces"
        app.pending_workspace = Path("/tmp/pcr-test/workspace")
        app.pending_execution_args = argparse.Namespace(**vars(app.args))
        app.agents[2] = tui_textual.AgentPane(
            idx=2,
            role="synthesis",
            status="queued",
            input_text="original request",
            execution_prompt="original request",
            developer_instructions="internal synthesis instructions",
        )
        app.candidate_batches.append(
            tui_textual.CandidateBatch(
                [2],
                {2},
                role="synthesis",
                prompt="original request",
                developer_instructions="internal synthesis instructions",
            )
        )
        app._post_progress = lambda _event: None

        with mock.patch.object(
            tui_textual,
            "run_additional_agents",
            return_value=[],
        ) as retry:
            with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                self.assertTrue(app._launch_next_candidate_batch())
                thread_cls.call_args.kwargs["target"]()

        call = retry.call_args.kwargs
        self.assertEqual(call["prompt"], "original request")
        self.assertEqual(call["agent_role"], "synthesis")
        self.assertEqual(call["resume_session_id"], "candidate-session")
        self.assertEqual(
            call["developer_instructions"],
            "internal synthesis instructions",
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_diff_view_loads_and_toggles_full_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            (baseline / "file.txt").write_text("before\n", encoding="utf-8")
            (candidate / "file.txt").write_text("after\n", encoding="utf-8")
            app = tui_textual.PcrTextualApp(parse_args(["-n", "1"]))
            app._sync = lambda: None
            app.pending_workspace = baseline
            app.pending_workspaces_root = root / "workspaces"
            app.agents[1].result = make_agent_result_data(1, candidate)
            messages: list[object] = []
            app.call_from_thread = lambda _callback, message: messages.append(message)

            with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                app._handle_command("/diff")
                thread_cls.call_args.kwargs["target"]()
            app._on_agent_diff_loaded(messages[0])

            self.assertTrue(app.agents[1].show_diff)
            self.assertIn("-before", app._detail_text())
            self.assertIn("+after", app._detail_text())
            self.assertIn("diff", app._detail_title(app.agents[1]))
            self.assertEqual(
                app._diff_renderable(app.agents[1]).plain,
                app.agents[1].diff_text,
            )

            app._handle_command("/diff")
            self.assertFalse(app.agents[1].show_diff)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_diff_ignores_result_after_view_is_closed(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "1"]))
        app._sync = lambda: None
        app.pending_workspace = Path("/tmp/baseline")
        app.pending_workspaces_root = Path("/tmp/run/workspaces")
        app.agents[1].result = make_agent_result_data(1, Path("/tmp/candidate"))

        with mock.patch.object(tui_textual.threading, "Thread"):
            app._handle_command("/diff")
        request_id = app.agents[1].diff_request
        app._handle_command("/diff")
        app._on_agent_diff_loaded(
            tui_textual.AgentDiffLoaded(1, request_id, "stale patch")
        )

        self.assertFalse(app.agents[1].show_diff)
        self.assertFalse(app.agents[1].diff_loading)
        self.assertEqual(app.agents[1].diff_text, "")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_rings_for_first_success_and_all_complete_only_in_background(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.running = True
        app.app_in_foreground = False
        app.pending_execution_args = argparse.Namespace(recommend_by="reasoning_tokens")
        with mock.patch.object(app, "bell") as bell:
            for idx in (1, 2):
                app._on_runner_event(
                    tui_textual.RunnerEvent(
                        {
                            "type": "agent_finished",
                            "idx": idx,
                            "result": make_agent_result_data(idx, Path(f"/tmp/agent-{idx}")),
                        }
                    )
                )
            app._on_runner_event(
                tui_textual.RunnerEvent(
                    {
                        "type": "run_finished",
                        "run_root": "/tmp/pcr-test/run",
                        "best_agent": 1,
                        "cancelled": False,
                    }
                )
            )

        self.assertEqual(bell.call_count, 2)

        foreground_app = tui_textual.PcrTextualApp(parse_args(["-n", "1"]))
        foreground_app._sync = lambda: None
        foreground_app.app_in_foreground = True
        with mock.patch.object(foreground_app, "bell") as foreground_bell:
            foreground_app._on_runner_event(
                tui_textual.RunnerEvent(
                    {
                        "type": "agent_finished",
                        "idx": 1,
                        "result": make_agent_result_data(1, Path("/tmp/foreground-agent")),
                    }
                )
            )
        foreground_bell.assert_not_called()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_workspace_change_finalizes_displayed_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current_workspace = root / "current"
            next_workspace = root / "next"
            current_workspace.mkdir()
            next_workspace.mkdir()
            app = tui_textual.PcrTextualApp(
                parse_args(["--workspace", str(current_workspace), "-n", "5"])
            )
            app._sync = lambda: None
            app._show_text = lambda _text: None
            app.pending_workspaces_root = root / "run" / "workspaces"
            app.pending_workspace = current_workspace.resolve()
            app.pending_no_sync_back = False
            app.recommended_agent = 5
            app.selected_agent = 3
            app.agents[3].result = {"status": "success"}

            def finalize_selected(*_args: object, **_kwargs: object) -> bool:
                app._clear_pending_run()
                return True

            with mock.patch.object(
                app,
                "_finalize_agent",
                side_effect=finalize_selected,
            ) as finalize:
                app._handle_workspace([str(next_workspace)])

        finalize.assert_called_once_with(
            3,
            require_resume=False,
            archive_detail=True,
        )
        self.assertEqual(app.workspace, next_workspace.resolve())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_resume_change_finalizes_displayed_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "5"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.recommended_agent = 5
        app.selected_agent = 3
        app.agents[3].result = {"status": "success"}
        app.resume_choices_loaded = True
        app.resume_entries = [
            app_core.ResumeSession(
                session_id="session-next",
                title="next conversation",
                cwd=str(app.workspace),
                updated_at=1,
                rollout_path="/tmp/session-next.jsonl",
            )
        ]

        def finalize_selected(*_args: object, **_kwargs: object) -> bool:
            app._clear_pending_run()
            return True

        with mock.patch.object(
            app,
            "_finalize_agent",
            side_effect=finalize_selected,
        ) as finalize:
            with mock.patch.object(app, "_select_resume_session") as select_resume:
                app._handle_resume(["1"])

        finalize.assert_called_once_with(
            3,
            require_resume=False,
            archive_detail=True,
        )
        select_resume.assert_called_once_with(
            "session-next",
            "/tmp/session-next.jsonl",
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_kill_stops_only_selected_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "3"]))
        app._sync = lambda: None
        app.running = True
        app.cancel_event = threading.Event()
        app.agent_cancel_events = {
            idx: threading.Event() for idx in range(1, 4)
        }
        app.selected_agent = 2
        app.agents[2].status = "running"

        app._handle_command("/kill")

        self.assertFalse(app.cancel_event.is_set())
        self.assertFalse(app.agent_cancel_events[1].is_set())
        self.assertTrue(app.agent_cancel_events[2].is_set())
        self.assertFalse(app.agent_cancel_events[3].is_set())
        self.assertEqual(app.agents[2].status, "stopping")
        self.assertEqual(app.status, "Stopping AGENT-002; other agents continue")

        app._on_runner_event(
            tui_textual.RunnerEvent(
                {"type": "agent_status", "idx": 2, "status": "copying"}
            )
        )
        app._on_runner_event(
            tui_textual.RunnerEvent(
                {"type": "agent_line", "idx": 2, "text": "agent_message:last useful line"}
            )
        )
        self.assertEqual(app.agents[2].status, "stopping")
        self.assertEqual(app.agents[2].output_lines, ["last useful line"])

        app._on_runner_event(
            tui_textual.RunnerEvent(
                {
                    "type": "agent_finished",
                    "idx": 2,
                    "result": {"status": "killed", "reasoning_tokens": 12},
                }
            )
        )
        self.assertEqual(app.agents[2].status, "killed")
        self.assertIn("killed", app._detail_title(app.agents[2]))

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_kill_accepts_agent_selector_and_rejects_finished_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "3"]))
        app._sync = lambda: None
        app.running = True
        app.cancel_event = threading.Event()
        app.agent_cancel_events = {
            idx: threading.Event() for idx in range(1, 4)
        }
        app.agents[1].result = {"status": "success"}
        app.agents[3].status = "running"

        app._handle_command("/kill agent-003")
        self.assertTrue(app.agent_cancel_events[3].is_set())
        self.assertEqual(app.agents[3].status, "stopping")

        app._handle_command("/kill 1")
        self.assertFalse(app.agent_cancel_events[1].is_set())
        self.assertEqual(app.status, "AGENT-001 has already finished")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_kill_rejects_queued_agent(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.running = True
        app.cancel_event = threading.Event()
        app.agent_cancel_events = {
            idx: threading.Event() for idx in range(1, 3)
        }
        app.selected_agent = 2
        app.agents[2].status = "queued"

        app._handle_command("/kill")

        self.assertFalse(app.agent_cancel_events[2].is_set())
        self.assertEqual(app.agents[2].status, "queued")
        self.assertEqual(
            app.status,
            "AGENT-002 is not running; queued agents will start normally",
        )

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
                    self.assertEqual(app.args.num_agents, 3)

                    synthesis_agents = app.query_one(
                        "#config-synthesis-agents"
                    )
                    synthesis_agents.value = "2"
                    synthesis_agents.focus()
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(app.synthesis_agents, 2)
                    self.assertEqual(app.args.synthesis_agents, 2)

                    max_parallel = app.query_one("#config-max-parallel")
                    max_parallel.value = "2"
                    max_parallel.focus()
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(app.args.max_parallel, 2)

                    subagents = app.query_one("#config-subagents")
                    subagents.value = True
                    await pilot.pause()

                    subagents_limit = app.query_one("#config-subagents-limit")
                    subagents_limit.value = "12"
                    subagents_limit.focus()
                    await pilot.press("enter")
                    await pilot.pause()

                    for selector, value in (
                        ("#config-execution", "serial"),
                        ("#config-recommend-by", "duration"),
                        ("#config-sync-back", False),
                        ("#config-keep-workspaces", True),
                    ):
                        control = app.query_one(selector)
                        control.focus()
                        control.value = value
                        await pilot.pause()

                    model = app.query_one("#config-model")
                    app._set_select_control(
                        model,
                        "",
                        [("default", ""), ("gpt-test", "gpt-test")],
                    )
                    model.value = "gpt-test"
                    await pilot.pause()

                    effort = app.query_one("#config-effort")
                    effort.value = "high"
                    await pilot.pause()

                    self.assertTrue(app.args.serial)
                    self.assertEqual(app.args.recommend_by, "duration")
                    self.assertEqual(app.args.model, "gpt-test")
                    self.assertEqual(app.args.effort, "high")
                    self.assertTrue(app.args.subagents)
                    self.assertEqual(app.args.subagents_limit, 12)
                    self.assertTrue(app.args.no_sync_back)
                    self.assertTrue(app.args.keep_workspaces)
                    self.assertGreaterEqual(len(app.command_history), 6)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_numeric_controls_commit_on_blur_and_before_run(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    prompt = app.query_one("#prompt")
                    agents = app.query_one("#config-agents")
                    agents.focus()
                    agents.value = "3"
                    prompt.focus()
                    await pilot.pause()

                    self.assertEqual(app.num_agents, 3)
                    self.assertEqual(app.args.num_agents, 3)
                    self.assertEqual(len(app.agents), 3)

                    max_parallel = app.query_one("#config-max-parallel")
                    max_parallel.focus()
                    max_parallel.value = "2"
                    prompt.focus()
                    await pilot.pause()

                    self.assertEqual(app.args.max_parallel, 2)

                    subagents_limit = app.query_one("#config-subagents-limit")
                    subagents_limit.focus()
                    subagents_limit.value = "10"
                    prompt.focus()
                    await pilot.pause()

                    self.assertEqual(app.args.subagents_limit, 10)

                    subagents = app.query_one("#config-subagents")
                    subagents.value = True
                    await pilot.pause()

                    recommend_by = app.query_one("#config-recommend-by")
                    recommend_by.value = "duration"
                    model = app.query_one("#config-model")
                    app._set_select_control(
                        model,
                        "",
                        [("default", ""), ("gpt-test", "gpt-test")],
                    )
                    model.value = "gpt-test"
                    await pilot.pause()

                    agents.focus()
                    agents.value = "4"
                    captured_args: list[argparse.Namespace] = []

                    def capture_run(run_args: argparse.Namespace, *_args: object, **_kwargs: object) -> int:
                        captured_args.append(run_args)
                        return 0

                    with mock.patch.object(tui_textual, "run_once", side_effect=capture_run):
                        with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                            self.assertTrue(app._start_run("question"))
                            target = thread_cls.call_args.kwargs["target"]
                            target()

                    self.assertEqual(app.num_agents, 4)
                    self.assertEqual(len(app.agents), 4)
                    self.assertEqual(captured_args[0].num_agents, 4)
                    self.assertEqual(captured_args[0].synthesis_agents, 2)
                    self.assertEqual(captured_args[0].max_parallel, 2)
                    self.assertTrue(captured_args[0].subagents)
                    self.assertEqual(captured_args[0].subagents_limit, 10)
                    self.assertEqual(captured_args[0].recommend_by, "duration")
                    self.assertEqual(captured_args[0].model, "gpt-test")
                    self.assertEqual(
                        set(captured_args[0].agent_cancel_events),
                        {1, 2, 3, 4, 5, 6},
                    )
                    app.running = False

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
    def test_tui_ctrl_q_uses_pcr_cleanup_path(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
            app.args.no_sync_back = True
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                with mock.patch.object(app, "_cleanup_after_pending_run", return_value=True) as cleanup:
                    async with app.run_test() as pilot:
                        await pilot.press("ctrl+q")
                        await pilot.pause()

            cleanup.assert_called_once_with()
            self.assertFalse(app._has_pending_run())

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_ctrl_q_cancels_active_run_before_exit(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        app.running = True
        app.cancel_event = threading.Event()
        app._sync = lambda: None
        with mock.patch.object(app, "exit") as exit_app:
            asyncio.run(app.action_quit())

        self.assertTrue(app.cancel_event.is_set())
        self.assertTrue(app.exit_after_run)
        exit_app.assert_not_called()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_shutdown_waits_for_runner_and_cleans_pending_workspaces(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.cancel_event = threading.Event()
        runner_started = threading.Event()

        def runner() -> None:
            runner_started.set()
            app.cancel_event.wait(2)

        app.runner_thread = threading.Thread(target=runner, daemon=False)
        app.runner_thread.start()
        self.assertTrue(runner_started.wait(1))

        with mock.patch.object(app, "_cleanup_after_pending_run", return_value=True) as cleanup:
            app._shutdown_runner_and_cleanup()

        self.assertTrue(app.cancel_event.is_set())
        self.assertFalse(app.runner_thread.is_alive())
        cleanup.assert_called_once_with()
        self.assertFalse(app._has_pending_run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_runner_records_cleanup_path_even_after_ui_stops(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        run_root = Path("/tmp/pcr-test/run")
        with mock.patch.object(app, "call_from_thread", side_effect=RuntimeError("UI stopped")):
            app._post_progress(
                {
                    "type": "run_prepared",
                    "rows": [
                        ["RUNS_ROOT", str(run_root)],
                        ["WORKSPACE COPIES", str(run_root / "workspaces")],
                    ],
                }
            )

        self.assertEqual(app.pending_run_root, run_root)
        self.assertEqual(app.pending_workspaces_root, run_root / "workspaces")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_tui_shutdown_removes_registered_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            run_base = root / "runs"
            workspaces_root = run_base / "workspaces"
            candidate = workspaces_root / "agent_001"
            workspace.mkdir()
            subprocess.run(
                ["git", "-c", "init.defaultBranch=main", "init"],
                cwd=workspace,
                check=True,
                stdout=subprocess.PIPE,
            )
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=workspace, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
            (workspace / "tracked.txt").write_text("base", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, stdout=subprocess.PIPE)
            copy_workspace(workspace, candidate, run_base)

            args = parse_args(["--workspace", str(workspace)])
            app = tui_textual.PcrTextualApp(args)
            app.pending_workspaces_root = workspaces_root
            app._shutdown_runner_and_cleanup()

            listed = subprocess.run(
                ["git", "-C", str(workspace), "worktree", "list", "--porcelain"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
            self.assertNotIn(str(candidate), listed)
            self.assertFalse(workspaces_root.exists())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_shutdown_respects_keep_workspaces(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["--keep-workspaces"]))
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        with mock.patch.object(tui_textual, "cleanup_workspace_copies") as cleanup:
            app._shutdown_runner_and_cleanup()

        cleanup.assert_not_called()

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_run_textual_tui_cleans_up_when_textual_run_raises(self) -> None:
        app = mock.Mock()
        app.run.side_effect = RuntimeError("TUI failed")
        with mock.patch.object(tui_textual, "PcrTextualApp", return_value=app):
            with self.assertRaisesRegex(RuntimeError, "TUI failed"):
                tui_textual.run_textual_tui(parse_args([]))

        app._shutdown_runner_and_cleanup.assert_called_once_with()

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
                copied.clear()
                prompt.action_copy()

                self.assertEqual(len(copied), 1)
                self.assertIn("detail question", copied[0])
                self.assertIn("detail answer", copied[0])
                self.assertNotEqual(copied[0], "prompt")

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_detail_selection_copies_on_mouse_up_and_survives_refresh(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(80, 30)) as pilot:
                pane = app.agents[1]
                pane.input_text = "copy this detail"
                pane.final_text = "and this answer"
                app._mark_detail_dirty(pane)
                app._sync()
                await pilot.pause()

                copied: list[str] = []
                app.copy_to_clipboard = copied.append
                self.assertTrue(await pilot.double_click("#detail", offset=(3, 0)))
                await pilot.pause()

                selected = app.screen.get_selected_text()
                self.assertIsNotNone(selected)
                self.assertIn("copy this detail", selected or "")
                self.assertIn("and this answer", selected or "")
                self.assertIn(selected, copied)

                app.screen.clear_selection()
                app._sync()
                app.action_copy_selection()
                self.assertEqual(copied[-1], selected)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_runner_text_selection_uses_global_copy_handler(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(100, 30)) as pilot:
                copied: list[str] = []
                app.copy_to_clipboard = copied.append
                workspace_text = app.query_one("#runner-workspace").content

                self.assertTrue(await pilot.double_click("#runner-workspace", offset=(3, 0)))
                await pilot.pause()

                self.assertIn(str(workspace_text), app.screen.get_selected_text() or "")
                self.assertIn(str(workspace_text), copied)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_selection_drag_defers_rendering_until_mouse_up(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(80, 30)) as pilot:
                pane = app.agents[1]
                pane.input_text = "stable selection"
                pane.final_text = "initial answer"
                app._mark_detail_dirty(pane)
                app._sync()
                await pilot.pause()

                app.copy_to_clipboard = lambda _text: None
                self.assertTrue(await pilot.mouse_down("#detail", offset=(3, 0)))
                self.assertTrue(await pilot.hover("#detail", offset=(16, 2)))
                selected = app.screen.get_selected_text()
                self.assertIn("stable selection", selected or "")
                self.assertTrue(app.screen._selecting)

                detail = app.query_one("#detail")
                tips = app.query_one("#tips")
                displayed_tip = tips.content.plain
                pane.append("new live output", "output")
                app._mark_detail_dirty(pane)
                app._advance_tip()
                app._advance_tip_icon()
                app._sync()
                await pilot.pause()

                self.assertEqual(app.screen.get_selected_text(), selected)
                self.assertNotIn("new live output", detail.content.plain)
                self.assertEqual(tips.content.plain, displayed_tip)
                self.assertTrue(app._sync_deferred_for_selection)
                self.assertTrue(app._tip_refresh_deferred_for_selection)

                self.assertTrue(await pilot.mouse_up("#detail", offset=(16, 2)))
                await pilot.pause()

                self.assertFalse(app.screen._selecting)
                self.assertIn("new live output", detail.content.plain)
                self.assertIn(app.current_tip, tips.content.plain)
                self.assertFalse(app._sync_deferred_for_selection)
                self.assertFalse(app._tip_refresh_deferred_for_selection)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_fresh_click_releases_stale_mouse_capture(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test() as pilot:
                prompt = app.query_one("#prompt")
                runner_value = app.query_one("#runner-workspace")
                prompt.capture_mouse()
                await pilot.pause()
                self.assertIs(app.mouse_captured, prompt)

                app.post_message(tui_textual.events.AppBlur())
                await pilot.pause()
                self.assertIsNone(app.mouse_captured)
                self.assertFalse(app.app_in_foreground)

                app.post_message(tui_textual.events.AppFocus())
                await pilot.pause()
                self.assertTrue(app.app_in_foreground)

                prompt.capture_mouse()
                await pilot.pause()
                app._release_stale_mouse_capture(runner_value)
                await pilot.pause()

                self.assertIsNone(app.mouse_captured)

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
    def test_tui_macos_copy_does_not_emit_duplicate_osc52_payload(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        with mock.patch.object(tui_textual.sys, "platform", "darwin"):
            with mock.patch.object(tui_textual.shutil, "which", return_value="/usr/bin/pbcopy"):
                with mock.patch.object(tui_textual.subprocess, "run"):
                    with mock.patch.object(tui_textual.App, "copy_to_clipboard") as fallback:
                        app.copy_to_clipboard("large accumulated detail")

        fallback.assert_not_called()
        self.assertEqual(app._clipboard, "large accumulated detail")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_macos_copy_failure_does_not_flood_terminal_with_osc52(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        with mock.patch.object(tui_textual.sys, "platform", "darwin"):
            with mock.patch.object(tui_textual.shutil, "which", return_value="/usr/bin/pbcopy"):
                with mock.patch.object(
                    tui_textual.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("pbcopy", 2),
                ):
                    with mock.patch.object(tui_textual.App, "copy_to_clipboard") as fallback:
                        app.copy_to_clipboard("large accumulated detail")

        fallback.assert_not_called()
        self.assertEqual(app._clipboard, "large accumulated detail")

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
    def test_tui_resume_history_reflows_when_terminal_expands(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            app.detail_history = [
                app._history_detail_block(
                    app_core.CodexHistoryEntry("output", "中" * 70)
                )
            ]
            async with app.run_test(size=(60, 30)) as pilot:
                app._mark_detail_dirty()
                app._sync()
                await pilot.pause()
                detail = app.query_one("#detail")
                narrow_line_breaks = detail.content.plain.count("\n")

                await pilot.resize_terminal(120, 30)
                await pilot.pause()

                self.assertLess(
                    detail.content.plain.count("\n"),
                    narrow_line_breaks,
                )

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
            app._request_run_with_storage_check = (
                lambda prompt, record_history=False: prompts.append(prompt) or True
            )

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

    def test_display_line_shows_command_and_completion_without_output(self) -> None:
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

        self.assertEqual(
            display_line_parts_from_output(json.dumps(started)),
            ("command", "/bin/zsh -lc 'pytest -q'"),
        )
        self.assertEqual(
            display_line_parts_from_output(json.dumps(completed)),
            ("command", "/bin/zsh -lc 'pytest -q'\n✓ exit 0"),
        )
        self.assertEqual(
            tui_textual.command_detail_display(
                display_line_parts_from_output(json.dumps(completed))[1]
            ),
            ("Ran pytest -q\n└ completed (exit 0)", "success"),
        )

    def test_display_line_ignores_long_command_output(self) -> None:
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
            ("command", "/bin/zsh -lc 'pytest -q'\n✓ exit 0"),
        )

    def test_display_line_shows_failed_command_without_output(self) -> None:
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

        self.assertEqual(category, "command")
        self.assertEqual(text, "python big.py\n✗ exit 1")
        self.assertNotIn(long_line, text)
        self.assertEqual(
            tui_textual.command_detail_display(text),
            ("Ran python big.py\n└ failed (exit 1)", "failed"),
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_keeps_agent_messages_and_merges_command_completion(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        pane = app.agents[1]
        command = "python - <<'PY'\nprint('hello')\nPY"
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "I will inspect the project."}},
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": command, "status": "in_progress"},
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": command,
                    "aggregated_output": "many\ncommand\noutput\nlines\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {"type": "item.completed", "item": {"type": "agent_message", "text": "The implementation is here."}},
        ]

        for event in events:
            app._on_runner_event(
                tui_textual.RunnerEvent(
                    {"type": "agent_line", "idx": 1, "text": json.dumps(event)}
                )
            )

        self.assertEqual(
            pane.output_lines,
            ["I will inspect the project.", "The implementation is here."],
        )
        self.assertEqual(pane.lines, [f"{command}\n✓ exit 0"])
        self.assertNotIn("many", "\n".join(pane.lines))
        self.assertNotIn("output", "\n".join(pane.lines))
        timeline = app._current_attempt_blocks(pane)
        self.assertEqual([prefix for prefix, _text, _style in timeline], ["◇", "•", "◇"])
        self.assertEqual(timeline[0][1], "I will inspect the project.")
        self.assertEqual(
            timeline[1][1],
            "Ran python - <<'PY'\n"
            "│ print('hello')\n"
            "│ PY\n"
            "└ completed (exit 0)",
        )
        self.assertEqual(timeline[1][2], "command-success")
        self.assertEqual(timeline[2][1], "The implementation is here.")

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_command_cell_animates_and_keeps_a_stable_layout(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        pane = app.agents[1]
        pane.status = "running"
        pane.append("/bin/zsh -lc 'pytest -q'", "command")

        app.work_frame = 0
        running = app._current_attempt_blocks(pane)[0]
        first_key = app._detail_cache_key_for(pane)
        app.work_frame = 1
        second_key = app._detail_cache_key_for(pane)

        self.assertEqual(running[0], tui_textual.COMMAND_SPINNER_FRAMES[0])
        self.assertEqual(running[1], "Running pytest -q")
        self.assertEqual(running[2], "command-running")
        self.assertNotEqual(first_key, second_key)

        pane.append("/bin/zsh -lc 'pytest -q'\n✓ exit 0", "command")
        completed = app._current_attempt_blocks(pane)[0]
        self.assertEqual(completed, ("•", "Ran pytest -q\n└ completed (exit 0)", "command-success"))
        app._mark_detail_dirty(pane)
        rendered = app._detail_renderable()
        self.assertIn("• Ran pytest -q\n  └ completed (exit 0)", rendered.plain)
        self.assertNotIn("/bin/zsh -lc", rendered.plain)
        self.assertTrue(any(str(span.style) == "bold green" for span in rendered.spans))

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
    def test_tui_start_run_continues_from_selected_not_recommended_agent(self) -> None:
        args = parse_args([])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app._show_text = lambda _text: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.recommended_agent = 5
        app.selected_agent = 4
        app._handle_numofagents(["2"])

        with mock.patch.object(app, "_finalize_agent", return_value=True) as finalize:
            with mock.patch.object(tui_textual.threading, "Thread") as thread_cls:
                thread_cls.return_value.start.return_value = None
                app._start_run("next question")

        finalize.assert_called_once_with(4, archive_detail=True)
        self.assertEqual(len(app.agents), 2)
        self.assertEqual(app.selected_agent, 2)

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
            with mock.patch.object(
                app,
                "_request_run_with_storage_check",
                side_effect=continued.append,
            ):
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
    def test_tui_queues_follow_up_when_selected_agent_is_still_running(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
            with mock.patch.object(tui_textual, "list_resume_sessions", return_value=[]):
                async with app.run_test() as pilot:
                    app.running = True
                    app.cancel_event = threading.Event()
                    prompt = app.query_one("#prompt")
                    prompt.text = "follow-up question"
                    prompt.focus()
                    await pilot.press("enter")
                    await pilot.pause()

                    queue_frame = app.query_one("#follow-up-queue-frame")
                    queue = app.query_one("#follow-up-queue")
                    self.assertEqual(prompt.text, "")
                    self.assertEqual(
                        [item.prompt for item in app.follow_up_queue],
                        ["follow-up question"],
                    )
                    self.assertFalse(app.cancel_event.is_set())
                    self.assertTrue(queue_frame.display)
                    self.assertLess(app.query_one("#tips").region.y, queue_frame.region.y)
                    self.assertLess(queue_frame.region.y, prompt.region.y)
                    self.assertIn("follow-up question", str(queue.content))
                    self.assertIn("current agents continue", app.status)

                    prompt.text = "second follow-up"
                    await pilot.press("enter")
                    await pilot.pause()

                    self.assertEqual(
                        [item.prompt for item in app.follow_up_queue],
                        ["follow-up question", "second follow-up"],
                    )
                    self.assertFalse(app.cancel_event.is_set())
                    self.assertIn("second follow-up", str(queue.content))

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_completed_run_selects_recommended_agent_and_delays_queued_follow_up(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.running = True
        app.selected_agent = 1
        app.pending_execution_args = argparse.Namespace(
            recommend_by="reasoning_tokens"
        )
        app.follow_up_queue.append(
            tui_textual.QueuedFollowUp("follow-up question", True)
        )
        app.agents[1].result = make_agent_result_data(
            1,
            Path("/tmp/agent-1"),
            reasoning_tokens=100,
        )
        app.agents[2].result = make_agent_result_data(
            2,
            Path("/tmp/agent-2"),
            reasoning_tokens=200,
        )

        with mock.patch.object(tui_textual.time, "monotonic", return_value=100.0):
            app._on_runner_event(
                tui_textual.RunnerEvent(
                    {
                        "type": "run_finished",
                        "run_root": "/tmp/pcr-test/run",
                        "best_agent": 2,
                    }
                )
            )

        self.assertEqual(app.recommended_agent, 2)
        self.assertEqual(app.selected_agent, 2)
        self.assertEqual(app.follow_up_continue_at, 160.0)
        self.assertIn("in 60s", app.status)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_delayed_follow_up_uses_agent_displayed_at_deadline(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.pending_no_sync_back = False
        app.follow_up_queue.append(
            tui_textual.QueuedFollowUp("follow-up question", True)
        )
        app.follow_up_continue_at = 10.0
        app.selected_agent = 1
        app.agents[1].result = make_agent_result_data(
            1,
            Path("/tmp/agent-1"),
        )
        app.agents[2].result = make_agent_result_data(
            2,
            Path("/tmp/agent-2"),
        )

        # The user switches away from the initially recommended Agent during
        # the one-minute review window.
        app.selected_agent = 2
        with mock.patch.object(tui_textual.time, "monotonic", return_value=11.0):
            with mock.patch.object(app, "_finalize_agent", return_value=True) as finalize:
                with mock.patch.object(
                    app,
                    "_request_run_with_storage_check",
                    return_value=True,
                ) as start:
                    app._tick()

        finalize.assert_called_once_with(2, archive_detail=True)
        start.assert_called_once_with(
            "follow-up question",
            record_history=True,
            from_follow_up_queue=True,
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_run_finished_does_not_auto_switch_to_recommended_agent(self) -> None:
        args = parse_args(["-n", "5"])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app.selected_agent = 2

        app._on_runner_event(tui_textual.RunnerEvent({"type": "run_finished", "run_root": "/tmp/pcr-test/run", "best_agent": 5}))

        self.assertEqual(app.selected_agent, 2)
        self.assertEqual(app.recommended_agent, 5)

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_start_run_discards_no_success_pending_run(self) -> None:
        args = parse_args([])
        app = tui_textual.PcrTextualApp(args)
        app._sync = lambda: None
        app.pending_workspaces_root = Path("/tmp/pcr-test/workspaces")
        app.recommended_agent = None

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
            app.recommended_agent = 5
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
    def test_tui_detail_title_shows_live_counts_and_completed_distribution(self) -> None:
        app = tui_textual.PcrTextualApp(parse_args([]))
        pane = app.agents[1]
        pane.reasoning_tokens = 571497
        pane.reasoning_token_counts = {516: 2, 1024: 4}

        self.assertEqual(
            app._detail_title(pane),
            "AGENT-001, reasoning_tokens=5128(1024:4, 516:2), ←/→ switch",
        )

        pane.result = {"seconds": 1.0}
        pane.reasoning_token_counts = {516: 2, 1024: 2, 1204: 4}
        self.assertEqual(
            app._detail_title(pane),
            (
                "AGENT-001, seconds=1.00s, "
                "reasoning_tokens=7896(1204:50%, 1024:25%, 516:25%, total:8), "
                "←/→ switch"
            ),
        )

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_recommended_detail_uses_animated_rainbow_border(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args(["-n", "2"]))
            app.agents[1].input_text = "recommended"
            app.agents[2].input_text = "other"
            app.recommended_agent = 1
            with mock.patch.object(
                tui_textual,
                "RECOMMEND_BORDER_REFRESH_SECONDS",
                3600,
            ):
                async with app.run_test(size=(100, 36)):
                    app._sync()
                    frame = app.query_one("#detail-frame")
                    detail = app.query_one("#detail")
                    rainbow = (
                        frame.styles.border_top,
                        frame.styles.border_right,
                        frame.styles.border_bottom,
                        frame.styles.border_left,
                    )

                    self.assertTrue(frame.border_title.startswith("★ AGENT-001"))
                    self.assertEqual(frame.styles.border_title_align, "left")
                    self.assertTrue(frame.styles.border_title_style.bold)
                    self.assertEqual(len({str(edge[1]) for edge in rainbow}), 4)
                    self.assertEqual(detail.styles.border_left[0], "")

                    app.recommend_border_frame = 0
                    app.recommend_border_edge_index = 0
                    app._apply_recommend_border_colors(frame)
                    expected_colors = app._recommend_border_colors(1)
                    edge_names = frame.BORDER_EDGES
                    for edge_index, edge_name in enumerate(edge_names):
                        before = tuple(
                            getattr(frame.styles, f"border_{name}")
                            for name in edge_names
                        )
                        frame._dirty_regions.clear()
                        frame._repaint_regions.clear()
                        app._advance_recommend_border()
                        after = tuple(
                            getattr(frame.styles, f"border_{name}")
                            for name in edge_names
                        )
                        self.assertEqual(
                            str(after[edge_index][1]),
                            str(tui_textual.Color.parse(expected_colors[edge_index])),
                        )
                        for unchanged_index in range(len(edge_names)):
                            if unchanged_index != edge_index:
                                self.assertEqual(
                                    after[unchanged_index],
                                    before[unchanged_index],
                                )
                        dirty_regions = tuple(frame._repaint_regions)
                        self.assertEqual(len(dirty_regions), 1)
                        if edge_name in {"top", "bottom"}:
                            self.assertEqual(
                                dirty_regions[0].width,
                                frame.outer_size.width,
                            )
                            self.assertEqual(dirty_regions[0].height, 1)
                        else:
                            self.assertEqual(dirty_regions[0].width, 1)
                            self.assertEqual(
                                dirty_regions[0].height,
                                frame.outer_size.height - 2,
                            )
                        self.assertLess(
                            dirty_regions[0].area,
                            frame.outer_size.area,
                        )

                    self.assertEqual(app.recommend_border_frame, 1)
                    self.assertEqual(app.recommend_border_edge_index, 0)
                    paused_edges = tuple(
                        getattr(frame.styles, f"border_{name}")
                        for name in edge_names
                    )
                    with mock.patch.object(
                        app,
                        "_screen_selection_active",
                        return_value=True,
                    ):
                        app._advance_recommend_border()
                    self.assertEqual(
                        tuple(
                            getattr(frame.styles, f"border_{name}")
                            for name in edge_names
                        ),
                        paused_edges,
                    )
                    self.assertTrue(app._recommend_border_deferred_for_selection)
                    app._flush_selection_deferred_updates()
                    self.assertFalse(app._recommend_border_deferred_for_selection)

                    app.selected_agent = 2
                    app._sync()
                    normal = (
                        frame.styles.border_top,
                        frame.styles.border_right,
                        frame.styles.border_bottom,
                        frame.styles.border_left,
                    )
                    self.assertFalse(frame.border_title.startswith("★"))
                    self.assertEqual(len({str(edge[1]) for edge in normal}), 1)
                    self.assertEqual(detail.styles.border_left[0], "")

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
                self.assertEqual(app.query_one("#config-agents").value, "4")
                self.assertEqual(
                    app.query_one("#config-synthesis-agents").value,
                    "2",
                )
                self.assertEqual(app.query_one("#config-max-parallel").value, "4")
                self.assertFalse(app.query_one("#config-subagents").value)
                self.assertEqual(app.query_one("#config-subagents-limit").value, "8")
                self.assertEqual(app.query_one("#config-execution").value, "parallel")
                self.assertEqual(
                    app.query_one("#config-recommend-by").value,
                    "reasoning_tokens",
                )
                labels = [label for label, _value in app._tree_rows()]
                self.assertEqual(
                    labels[:3],
                    ["CONVERSATION", "CODEX_BIN", "WORKSPACE"],
                )
                runner_children = list(app.query_one("#runner-grid").children)
                child_ids = [child.id for child in runner_children]
                self.assertLess(
                    child_ids.index("runner-conversation"),
                    child_ids.index("runner-codex-bin"),
                )
                self.assertLess(
                    child_ids.index("runner-codex-bin"),
                    child_ids.index("runner-workspace"),
                )
                self.assertIn("RECOMMEND_BY", labels)
                self.assertIn("SYNTHESIS_AGENTS", labels)
                self.assertIn("SUBAGENTS", labels)
                self.assertIn("SUBAGENTS_LIMIT", labels)
                self.assertLess(
                    labels.index("MAX_PARALLEL"),
                    labels.index("SUBAGENTS_LIMIT"),
                )
                self.assertNotIn("BEST_BY", labels)
                self.assertNotIn("METADATA", labels)
                self.assertNotIn("WORKSPACE COPIES", [label for label, _value in app._tree_rows()])
                self.assertNotIn("MODULE_DIR", [label for label, _value in app._tree_rows()])
                self.assertNotIn("RUN_ANCHOR", [label for label, _value in app._tree_rows()])
                self.assertEqual(len(app.query("#runner-module-dir")), 0)
                self.assertEqual(len(app.query("#runner-run-anchor")), 0)

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_long_resume_title_does_not_leave_gap_before_recommendation(self) -> None:
        async def run() -> None:
            app = tui_textual.PcrTextualApp(parse_args([]))
            async with app.run_test(size=(100, 40)) as pilot:
                resume = app.query_one("#config-resume")
                app.resume_session_id = "session-1"
                app.resume_choices = [("中" * 500, "session-1")]
                app._set_select_control(
                    resume,
                    "session-1",
                    app.resume_choices,
                )
                app.recommended_agent = 1
                app._sync()
                await pilot.pause()

                recommendation = app.query_one("#runner-recommended-agent-key")
                self.assertEqual(recommendation.region.y, resume.region.bottom)
                self.assertEqual(resume.query_one("SelectCurrent").region.height, 1)

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
    def test_tui_large_storage_estimate_can_ignore_prompt_before_copying(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                workspace.mkdir()
                history_path = root / "history.json"
                total = tui_textual.LARGE_RUN_STORAGE_WARNING_BYTES + 1
                estimate = app_core.RunStorageEstimate(
                    num_agents=2,
                    workspace_bytes_per_agent=total // 2,
                    workspace_copies_bytes=total,
                    metadata_bytes_per_agent=0,
                    metadata_bytes=0,
                    total_bytes=total,
                )
                with mock.patch.dict(
                    os.environ,
                    {"PCR_PROMPT_HISTORY_PATH": str(history_path)},
                ):
                    app = tui_textual.PcrTextualApp(
                        parse_args(["--workspace", str(workspace), "-n", "2"])
                    )
                with mock.patch.object(
                    tui_textual,
                    "list_resume_sessions",
                    return_value=[],
                ), mock.patch.object(
                    tui_textual,
                    "estimate_staged_run_storage",
                    return_value=estimate,
                ), mock.patch.object(
                    app,
                    "_start_run",
                    return_value=True,
                ) as start_run:
                    async with app.run_test(size=(90, 32)) as pilot:
                        self.assertTrue(app._submit_task_prompt("large question"))
                        for _ in range(100):
                            await pilot.pause()
                            if isinstance(app.screen, tui_textual.StorageWarningScreen):
                                break

                        self.assertIsInstance(
                            app.screen,
                            tui_textual.StorageWarningScreen,
                        )
                        start_run.assert_not_called()
                        await pilot.click("#storage-cancel")
                        await pilot.pause()

                        start_run.assert_not_called()
                        self.assertFalse(app.storage_preflight_inflight)
                        self.assertIn("Prompt ignored", app.status)
                        self.assertEqual(
                            app.prompt_history_store.entries(workspace, None),
                            [],
                        )

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_storage_confirmation_fails_when_disk_is_too_small(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                workspace.mkdir()
                total = tui_textual.LARGE_RUN_STORAGE_WARNING_BYTES + 1
                estimate = app_core.RunStorageEstimate(
                    num_agents=1,
                    workspace_bytes_per_agent=total,
                    workspace_copies_bytes=total,
                    metadata_bytes_per_agent=0,
                    metadata_bytes=0,
                    total_bytes=total,
                )
                app = tui_textual.PcrTextualApp(
                    parse_args(["--workspace", str(workspace)])
                )
                with mock.patch.object(
                    tui_textual,
                    "list_resume_sessions",
                    return_value=[],
                ), mock.patch.object(
                    tui_textual,
                    "estimate_staged_run_storage",
                    return_value=estimate,
                ), mock.patch.object(
                    tui_textual,
                    "available_storage_bytes",
                    return_value=total - 1,
                ), mock.patch.object(
                    app,
                    "_start_run",
                    return_value=True,
                ) as start_run:
                    async with app.run_test(size=(90, 32)) as pilot:
                        self.assertTrue(app._submit_task_prompt("large question"))
                        for _ in range(100):
                            await pilot.pause()
                            if isinstance(app.screen, tui_textual.StorageWarningScreen):
                                break
                        await pilot.click("#storage-continue")
                        await pilot.pause()

                        start_run.assert_not_called()
                        self.assertFalse(app.storage_preflight_inflight)
                        self.assertIn("insufficient disk space", app.status)
                        self.assertEqual(
                            app.query_one("#prompt").text,
                            "large question",
                        )

        asyncio.run(run())

    @unittest.skipIf(getattr(tui_textual, "PcrTextualApp", None) is None, "textual is not installed")
    def test_tui_small_storage_estimate_starts_without_confirmation(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "workspace"
                workspace.mkdir()
                estimate = app_core.RunStorageEstimate(
                    num_agents=1,
                    workspace_bytes_per_agent=1024,
                    workspace_copies_bytes=1024,
                    metadata_bytes_per_agent=1024,
                    metadata_bytes=1024,
                    total_bytes=2048,
                )
                app = tui_textual.PcrTextualApp(
                    parse_args(["--workspace", str(workspace)])
                )
                with mock.patch.object(
                    tui_textual,
                    "list_resume_sessions",
                    return_value=[],
                ), mock.patch.object(
                    tui_textual,
                    "estimate_staged_run_storage",
                    return_value=estimate,
                ), mock.patch.object(
                    tui_textual,
                    "available_storage_bytes",
                    return_value=4096,
                ), mock.patch.object(
                    app,
                    "_start_run",
                    return_value=True,
                ) as start_run:
                    async with app.run_test() as pilot:
                        self.assertTrue(app._submit_task_prompt("small question"))
                        for _ in range(100):
                            await pilot.pause()
                            if start_run.called:
                                break

                        start_run.assert_called_once_with(
                            "small question",
                            record_history=True,
                        )
                        self.assertNotIsInstance(
                            app.screen,
                            tui_textual.StorageWarningScreen,
                        )

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


class ReasoningTokenTests(unittest.TestCase):
    @staticmethod
    def rollout_line(total: int) -> str:
        return json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "reasoning_output_tokens": total,
                        },
                        "last_token_usage": {
                            "reasoning_output_tokens": 999999,
                        },
                    },
                },
            }
        )

    def test_agent_state_counts_positive_reasoning_increments(self) -> None:
        state = AgentState(idx=1)
        state.seed_reasoning_total(500)

        for total in (1016, 2040, 2040, 2556, 3580):
            state.observe_reasoning_total(total)

        self.assertEqual(state.reasoning_tokens, 3580)
        self.assertEqual(state.reasoning_token_counts, {516: 2, 1024: 2})

    def test_rollout_monitor_uses_resume_baseline_and_waits_for_complete_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex_home"
            rollout = codex_home / "sessions" / "2026" / "07" / "12" / "rollout-session.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(self.rollout_line(500) + "\n", encoding="utf-8")
            state = AgentState(idx=3)
            events = []
            monitor = app_core.ReasoningRolloutMonitor(codex_home, state, events.append)

            with rollout.open("a", encoding="utf-8") as file:
                file.write(self.rollout_line(1016) + "\n")
                file.write(self.rollout_line(2040) + "\n")
                file.write(self.rollout_line(2556))

            self.assertTrue(monitor.poll())
            self.assertEqual(state.reasoning_token_counts, {516: 1, 1024: 1})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[-1]["reasoning_token_counts"], {516: 1, 1024: 1})

            with rollout.open("a", encoding="utf-8") as file:
                file.write("\n" + self.rollout_line(3580) + "\n")

            self.assertTrue(monitor.poll())
            self.assertEqual(state.reasoning_tokens, 3580)
            self.assertEqual(state.reasoning_token_counts, {516: 2, 1024: 2})
            self.assertEqual(events[-1]["reasoning_token_counts"], {516: 2, 1024: 2})

    def test_agent_result_normalizes_serialized_reasoning_counts(self) -> None:
        result = AgentResult(
            idx=1,
            workspace_dir="",
            meta_dir="",
            codex_home="",
            stdout_log="",
            stderr_log="",
            final_message="",
            command=[],
            returncode=0,
            status="success",
            seconds=1.0,
            reasoning_token_counts={"516": 2, "1024": 4},
        )

        self.assertEqual(result.reasoning_token_counts, {516: 2, 1024: 4})

    def test_run_one_agent_persists_rollout_increment_counts(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                meta = root / "meta"
                codex_home = root / "codex_home"
                workspace.mkdir()
                codex_home.mkdir()
                rollout_text = "\n".join(
                    self.rollout_line(total) for total in (516, 1540, 2056)
                ) + "\n"
                script = (
                    "import json, os\n"
                    "from pathlib import Path\n"
                    "rollout = Path(os.environ['CODEX_HOME']) / 'sessions' / '2026' / '07' / '12' / 'rollout-session-test.jsonl'\n"
                    "rollout.parent.mkdir(parents=True, exist_ok=True)\n"
                    f"rollout.write_text({rollout_text!r}, encoding='utf-8')\n"
                    "print(json.dumps({'type': 'thread.started', 'thread_id': 'session-test'}), flush=True)\n"
                )
                events = []

                result = await run_one_agent(
                    idx=1,
                    agent_workspace=workspace,
                    meta_dir=meta,
                    codex_home=codex_home,
                    prompt="test",
                    command=[sys.executable, "-c", script],
                    progress_callback=events.append,
                )

                self.assertEqual(result.status, "success")
                self.assertEqual(result.reasoning_tokens, 2056)
                self.assertEqual(result.reasoning_token_counts, {516: 2, 1024: 1})
                token_events = [event for event in events if event["type"] == "agent_tokens"]
                self.assertEqual(token_events[-1]["reasoning_token_counts"], {516: 2, 1024: 1})
                persisted = json.loads((meta / "status.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["reasoning_token_counts"], {"516": 2, "1024": 1})

        asyncio.run(run())

    def test_reasoning_title_formats_live_counts_and_final_percentages(self) -> None:
        self.assertEqual(
            tui_textual.format_reasoning_tokens_title(
                571497,
                {516: 1, 1024: 4},
                completed=False,
            ),
            "reasoning_tokens=4612(1024:4, 516:1)",
        )
        self.assertEqual(
            tui_textual.format_reasoning_tokens_title(
                571497,
                {516: 2, 1024: 2, 1204: 4},
                completed=True,
            ),
            "reasoning_tokens=7896(1204:50%, 1024:25%, 516:25%, total:8)",
        )

    def test_reasoning_title_ranks_contribution_and_keeps_total_last(self) -> None:
        counts = {10: 1, 50: 4, 100: 2, 200: 1, 300: 1, 400: 2}

        self.assertEqual(
            tui_textual.format_reasoning_tokens_title(
                591235,
                counts,
                completed=False,
            ),
            "reasoning_tokens=1710(400:2, 300:1, 200:1, 100:2, other:5)",
        )
        self.assertEqual(
            tui_textual.format_reasoning_tokens_title(
                591235,
                counts,
                completed=True,
            ),
            (
                "reasoning_tokens=1710(400:18.2%, 300:9.1%, 200:9.1%, "
                "100:18.2%, other:45.5%, total:11)"
            ),
        )


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

    def test_stream_to_log_preserves_full_agent_message_for_tui(self) -> None:
        async def run() -> None:
            message = "正在检查项目。\n" + ("完整的 Codex 对话内容。" * 300)
            raw = {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": message},
            }
            raw_line = json.dumps(raw, ensure_ascii=False).encode() + b"\n"
            reader = asyncio.StreamReader(limit=8)
            reader.feed_data(raw_line)
            reader.feed_eof()
            events = []
            state = AgentState(idx=1)
            with tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / "stdout.log"
                await stream_to_log(reader, log_path, state, "stdout", events.append)

            line_events = [event for event in events if event["type"] == "agent_line"]
            self.assertEqual(len(line_events), 1)
            self.assertEqual(line_events[0]["text"], raw_line.decode().strip())
            self.assertEqual(display_line_from_output(line_events[0]["text"]), message)

        asyncio.run(run())

    def test_stream_to_log_strips_command_output_from_tui_progress(self) -> None:
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
            progress_text = line_events[0]["text"]
            self.assertLess(len(progress_text), len(raw_line.decode()))
            self.assertNotIn("aggregated_output", progress_text)
            self.assertEqual(
                display_line_parts_from_output(progress_text),
                ("command", "pytest -q\n✓ exit 0"),
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

    def test_run_one_agent_can_be_killed_without_cancelling_run(self) -> None:
        async def run() -> None:
            cancel_event = threading.Event()
            agent_cancel_event = threading.Event()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                meta = root / "meta"
                codex_home = root / "codex_home"
                workspace.mkdir()
                codex_home.mkdir()

                async def kill_soon() -> None:
                    await asyncio.sleep(0.1)
                    agent_cancel_event.set()

                killer = asyncio.create_task(kill_soon())
                result = await run_one_agent(
                    idx=1,
                    agent_workspace=workspace,
                    meta_dir=meta,
                    codex_home=codex_home,
                    prompt="",
                    command=[sys.executable, "-c", "import time; time.sleep(10)"],
                    cancel_event=cancel_event,
                    agent_cancel_event=agent_cancel_event,
                )
                await killer

            self.assertEqual(result.status, "killed")
            self.assertFalse(cancel_event.is_set())
            self.assertLess(result.seconds, 3)

        asyncio.run(run())

    def test_run_all_agents_keeps_other_agents_running_after_kill(self) -> None:
        async def run() -> None:
            cancel_event = threading.Event()
            agent_cancel_events = {
                1: threading.Event(),
                2: threading.Event(),
            }
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspaces = root / "workspaces"
                meta = root / "meta"
                codex_homes = {
                    1: root / "codex_home_1",
                    2: root / "codex_home_2",
                }
                for idx in (1, 2):
                    (workspaces / f"agent_{idx:03d}").mkdir(parents=True)
                    codex_homes[idx].mkdir()

                async def kill_first_agent() -> None:
                    await asyncio.sleep(0.1)
                    agent_cancel_events[1].set()

                killer = asyncio.create_task(kill_first_agent())
                events: list[dict[str, object]] = []
                results = await app_core.run_all_agents(
                    n=2,
                    workspaces_root=workspaces,
                    meta_root=meta,
                    prompt="",
                    command_by_agent={
                        1: [sys.executable, "-c", "import time; time.sleep(10)"],
                        2: [sys.executable, "-c", "print('finished')"],
                    },
                    codex_home_by_agent=codex_homes,
                    max_parallel=2,
                    progress_callback=events.append,
                    cancel_event=cancel_event,
                    agent_cancel_events=agent_cancel_events,
                )
                await killer

            statuses = {result.idx: result.status for result in results}
            self.assertEqual(statuses, {1: "killed", 2: "success"})
            self.assertFalse(cancel_event.is_set())
            finished = {
                int(event["idx"]): event["result"]["status"]
                for event in events
                if event.get("type") == "agent_finished"
            }
            self.assertEqual(finished, {1: "killed", 2: "success"})

        asyncio.run(run())

    def test_run_all_agents_starts_queued_agent_after_running_agent_is_killed(self) -> None:
        async def run() -> None:
            agent_cancel_events = {
                1: threading.Event(),
                2: threading.Event(),
            }
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspaces = root / "workspaces"
                meta = root / "meta"
                codex_homes = {
                    1: root / "codex_home_1",
                    2: root / "codex_home_2",
                }
                for idx in (1, 2):
                    (workspaces / f"agent_{idx:03d}").mkdir(parents=True)
                    codex_homes[idx].mkdir()

                events: list[dict[str, object]] = []

                def record_event(event: dict[str, object]) -> None:
                    events.append(event)
                    if (
                        event.get("type") == "agent_status"
                        and event.get("status") == "queued"
                        and event.get("idx") == 2
                    ):
                        agent_cancel_events[2].set()
                    if event.get("type") == "agent_started" and event.get("idx") == 1:
                        agent_cancel_events[1].set()

                results = await app_core.run_all_agents(
                    n=2,
                    workspaces_root=workspaces,
                    meta_root=meta,
                    prompt="",
                    command_by_agent={
                        1: [sys.executable, "-c", "import time; time.sleep(10)"],
                        2: [sys.executable, "-c", "print('finished')"],
                    },
                    codex_home_by_agent=codex_homes,
                    max_parallel=1,
                    progress_callback=record_event,
                    agent_cancel_events=agent_cancel_events,
                )

            self.assertEqual(
                {result.idx: result.status for result in results},
                {1: "killed", 2: "success"},
            )
            self.assertEqual(
                [event.get("idx") for event in events if event.get("type") == "agent_started"],
                [1, 2],
            )

        asyncio.run(run())

    @unittest.skipUnless(os.name == "posix", "process-group cancellation requires POSIX")
    def test_run_one_agent_stops_descendant_processes_on_cancel(self) -> None:
        async def run() -> None:
            cancel_event = threading.Event()
            child_pid = 0
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                meta = root / "meta"
                codex_home = root / "codex_home"
                pid_file = root / "child.pid"
                workspace.mkdir()
                codex_home.mkdir()
                script = (
                    "import pathlib, subprocess, sys, time; "
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
                    "time.sleep(30)"
                )

                async def cancel_after_child_starts() -> None:
                    for _ in range(100):
                        if pid_file.exists():
                            cancel_event.set()
                            return
                        await asyncio.sleep(0.02)
                    self.fail("descendant process did not start")

                canceller = asyncio.create_task(cancel_after_child_starts())
                try:
                    result = await run_one_agent(
                        idx=1,
                        agent_workspace=workspace,
                        meta_dir=meta,
                        codex_home=codex_home,
                        prompt="",
                        command=[sys.executable, "-c", script],
                        cancel_event=cancel_event,
                    )
                    await canceller
                    child_pid = int(pid_file.read_text(encoding="utf-8"))
                    for _ in range(50):
                        try:
                            os.kill(child_pid, 0)
                        except ProcessLookupError:
                            break
                        await asyncio.sleep(0.02)
                    else:
                        self.fail("descendant process survived cancellation")
                finally:
                    if child_pid:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

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

    def test_remove_agent_codex_homes_deletes_isolated_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "meta"
            first = meta / "agent_001" / "codex_home"
            second = meta / "agent_002" / "codex_home"
            large_index = meta / "agent_1000" / "codex_home"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            large_index.mkdir(parents=True)
            (first / "state_5.sqlite").write_text("large", encoding="utf-8")
            (second / "sessions").mkdir()

            self.assertTrue(remove_agent_codex_homes(meta))
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertFalse(large_index.exists())
            self.assertTrue((meta / "agent_001").is_dir())

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
