from __future__ import annotations

import errno
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parallel_codex_runner_core import workspace as sync


class LiveWorkspaceSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.src = self.root / "candidate"
        self.dst = self.root / "original"
        for directory in (self.src, self.dst):
            directory.mkdir()
            self.git(directory, "init", "-q")
            (directory / ".gitignore").write_text(".local/\nnode_modules/\n*.log\n")
            self.git(directory, "add", ".gitignore")

    @staticmethod
    def git(directory: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)

    def test_running_sqlite_and_dependencies_are_never_replaced_or_deleted(self) -> None:
        for use_rsync in (False, True):
            if use_rsync and not shutil.which("rsync"):
                continue
            with self.subTest(rsync=use_rsync):
                for directory in (self.src, self.dst):
                    (directory / ".local").mkdir(exist_ok=True)
                    (directory / "node_modules").mkdir(exist_ok=True)
                    (directory / "node_modules" / "package.js").write_text(directory.name)
                database = self.dst / ".local" / "business.sqlite"
                with sqlite3.connect(database) as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("CREATE TABLE IF NOT EXISTS facts (value TEXT)")
                    connection.execute("INSERT INTO facts VALUES ('real data')")
                    connection.commit()
                    before = database.stat().st_ino
                    (self.src / ".local" / "business.sqlite").write_text("stale candidate database")
                    (self.src / "app.py").write_text("new code")
                    (self.dst / "removed.py").write_text("obsolete code")
                    with patch.object(sync, "_rsync_available", return_value=use_rsync):
                        sync._sync_workspace_files(self.src, self.dst)
                    self.assertEqual(database.stat().st_ino, before)
                    self.assertEqual(connection.execute("SELECT value FROM facts LIMIT 1").fetchone(), ("real data",))
                    connection.execute("INSERT INTO facts VALUES ('still writable')")
                self.assertEqual((self.dst / "app.py").read_text(), "new code")
                self.assertFalse((self.dst / "removed.py").exists())
                self.assertEqual((self.dst / "node_modules" / "package.js").read_text(), "original")

    def test_new_tracked_file_inside_ignored_directory_is_synced(self) -> None:
        for directory in (self.src, self.dst):
            (directory / ".local").mkdir()
            (directory / ".local" / "private.db").write_text(directory.name)
        (self.src / ".local" / "fixture.txt").write_text("deliberate deliverable")
        self.git(self.src, "add", "-f", ".local/fixture.txt")
        with patch.object(sync, "_rsync_available", return_value=False):
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / ".local" / "fixture.txt").read_text(), "deliberate deliverable")
        self.assertEqual((self.dst / ".local" / "private.db").read_text(), "original")

    def test_receipt_confirms_files_permissions_deletions_and_preserves_live_data(self) -> None:
        (self.src / "app.py").write_text("new code")
        (self.src / "app.py").chmod(0o755)
        (self.src / "link").symlink_to("app.py")
        (self.dst / "removed.py").write_text("obsolete")
        (self.dst / ".local").mkdir()
        runtime = self.dst / ".local" / "data.db"
        runtime.write_text("live")
        receipt = sync.capture_sync_receipt(self.src, self.dst)
        with self.assertRaisesRegex(RuntimeError, "Saved files differ"):
            sync.verify_sync_receipt(receipt, self.dst)
        sync._sync_workspace_files(self.src, self.dst)
        runtime.write_text("new live data")
        sync.verify_sync_receipt(receipt, self.dst)
        self.assertEqual(runtime.read_text(), "new live data")
        (self.dst / "app.py").chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "app.py"):
            sync.verify_sync_receipt(receipt, self.dst)
        self.assertEqual((self.dst / "app.py").stat().st_mode & 0o777, 0o644)

    def test_receipt_refuses_concurrent_edits_missing_evidence_and_changed_index(self) -> None:
        (self.src / "app.py").write_text("candidate")
        receipt = sync.capture_sync_receipt(self.src, self.dst)
        sync._sync_workspace_files(self.src, self.dst)
        (self.dst / "app.py").write_text("user changed this")
        with self.assertRaisesRegex(RuntimeError, "Saved files differ"):
            sync.verify_sync_receipt(receipt, self.dst)
        self.assertEqual((self.dst / "app.py").read_text(), "user changed this")
        with self.assertRaisesRegex(RuntimeError, "no complete verification"):
            sync.verify_sync_receipt({}, self.dst)
        (self.dst / "app.py").write_text("candidate")
        self.git(self.dst, "add", "app.py")
        with self.assertRaisesRegex(RuntimeError, "Git history or staged changes"):
            sync.verify_sync_receipt(receipt, self.dst)

    def test_receipt_rejects_replaced_destination_even_with_identical_files(self) -> None:
        receipt = sync.capture_sync_receipt(self.src, self.dst)
        moved = self.dst.with_name("previous-original")
        self.dst.rename(moved)
        shutil.copytree(moved, self.dst)
        with self.assertRaisesRegex(RuntimeError, "folder was replaced"):
            sync.verify_sync_receipt(receipt, self.dst)

    def test_receipt_verification_does_not_run_git_hooks_or_refresh_index(self) -> None:
        receipt = sync.capture_sync_receipt(self.dst, self.dst)
        marker = self.root / "hook-ran"
        hook = self.root / "fsmonitor-hook"
        hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
        hook.chmod(0o755)
        self.git(self.dst, "config", "core.fsmonitor", str(hook))
        index = self.dst / ".git" / "index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        sync.verify_sync_receipt(receipt, self.dst)
        self.assertFalse(marker.exists())
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)

    def test_deleted_parent_keeps_ignored_descendant_only(self) -> None:
        (self.dst / "old").mkdir()
        (self.dst / "old" / "server.log").write_text("live log")
        (self.dst / "old" / "obsolete.py").write_text("obsolete code")
        with patch.object(sync, "_rsync_available", return_value=False):
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / "old" / "server.log").read_text(), "live log")
        self.assertFalse((self.dst / "old" / "obsolete.py").exists())

    def test_replacing_parent_of_live_data_is_refused_before_mutation(self) -> None:
        (self.dst / "old").mkdir()
        (self.dst / "old" / "server.log").write_text("live log")
        (self.src / "old").write_text("replacement")
        with self.assertRaisesRegex(sync.WorkspaceSyncPreflightError, "local runtime data"):
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / "old" / "server.log").read_text(), "live log")

    def test_open_read_only_file_is_atomically_replaced_without_truncating_reader(self) -> None:
        (self.src / "app.py").write_text("new contents")
        (self.dst / "app.py").write_text("old contents")
        (self.dst / "app.py").chmod(0o444)
        with (self.dst / "app.py").open() as reader:
            with patch.object(sync, "_rsync_available", return_value=False):
                sync._sync_workspace_files(self.src, self.dst)
            self.assertEqual(reader.read(), "old contents")
        self.assertEqual((self.dst / "app.py").read_text(), "new contents")
        self.assertFalse(list(self.dst.glob(".pcr-sync-*")))

    def test_rsync_failure_uses_atomic_fallback_and_verifies_content(self) -> None:
        (self.src / "app.py").write_text("new contents")
        with patch.object(sync, "_rsync_available", return_value=True), patch.object(
            sync, "sync_back_with_rsync", side_effect=RuntimeError("rsync: busy file")
        ):
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / "app.py").read_text(), "new contents")

    def test_failed_fallback_keeps_both_diagnostics_and_never_claims_success(self) -> None:
        (self.src / "app.py").write_text("new contents")
        with patch.object(sync, "_rsync_available", return_value=True), patch.object(
            sync, "sync_back_with_rsync", side_effect=RuntimeError("rsync: app.py: permission denied")
        ) as rsync, patch.object(sync, "sync_back_with_python", side_effect=PermissionError(
            errno.EACCES, "Permission denied", str(self.dst / "app.py")
        )), patch.object(sync.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "rsync: app.py: permission denied; atomic copy failed"):
                sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual(rsync.call_count, 3)

    def test_transient_busy_replace_retries_and_leaves_no_temporary_files(self) -> None:
        (self.src / "app.py").write_text("new contents")
        replace = os.replace
        busy = True

        def transient(source, destination):
            nonlocal busy
            if Path(destination).name == "app.py" and busy:
                busy = False
                raise OSError(errno.EBUSY, "in use", str(destination))
            return replace(source, destination)

        with patch.object(sync, "_rsync_available", return_value=False), patch.object(
            sync.os, "replace", side_effect=transient
        ), patch.object(sync.time, "sleep"):
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / "app.py").read_text(), "new contents")
        self.assertFalse(list(self.dst.glob(".pcr-sync-*")))

    def test_success_exit_does_not_hide_missing_files(self) -> None:
        (self.src / "app.py").write_text("new contents")
        with patch.object(sync, "_rsync_available", return_value=True), patch.object(
            sync, "sync_back_with_rsync"
        ), patch.object(sync.time, "sleep"), self.assertRaisesRegex(RuntimeError, "app.py"):
            sync._sync_workspace_files(self.src, self.dst)

    def test_source_edit_during_copy_is_retried(self) -> None:
        source = self.src / "app.py"
        source.write_text("first")
        copy = sync._copy_sync_file
        modified = False

        def changing_source(src, dst):
            nonlocal modified
            copy(src, dst)
            if src == source and not modified:
                modified = True
                source.write_text("second")

        with patch.object(sync, "_rsync_available", return_value=False), patch.object(
            sync, "_copy_sync_file", side_effect=changing_source
        ), patch.object(sync.time, "sleep"):
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / "app.py").read_text(), "second")

    def test_rsync_retains_actual_error_output(self) -> None:
        result = subprocess.CompletedProcess([], 1, "", "rsync: cache.db: Permission denied")
        with patch.object(sync.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "cache.db: Permission denied"):
                sync.sync_back_with_rsync(self.src, self.dst)

    @unittest.skipUnless(shutil.which("rsync"), "rsync is not installed")
    def test_rsync_handles_literal_runtime_directory_names(self) -> None:
        for directory in (self.src, self.dst):
            (directory / "cache[1]").mkdir()
            (directory / "cache[1]" / "state").write_text(directory.name)
        (self.src / "app.py").write_text("new code")
        sync.sync_back_with_rsync(self.src, self.dst, (Path("cache[1]"),))
        self.assertEqual((self.dst / "app.py").read_text(), "new code")
        self.assertEqual((self.dst / "cache[1]" / "state").read_text(), "original")

    def test_unchanged_files_do_not_trigger_another_inode_replacement(self) -> None:
        (self.src / "app.py").write_text("same contents")
        with patch.object(sync, "_rsync_available", return_value=False):
            sync._sync_workspace_files(self.src, self.dst)
            inode = (self.dst / "app.py").stat().st_ino
            sync._sync_workspace_files(self.src, self.dst)
        self.assertEqual((self.dst / "app.py").stat().st_ino, inode)

    def test_directory_walk_errors_are_not_reported_as_success(self) -> None:
        def unreadable(root, *, onerror):
            onerror(PermissionError(errno.EACCES, "Permission denied", str(root / "private")))
            return iter(())

        with patch.object(sync.os, "walk", side_effect=unreadable), patch.object(sync.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "private"):
                sync._sync_workspace_files(self.src, self.dst)

    @unittest.skipUnless(Path("/proc").is_dir() or shutil.which("lsof"), "process cwd lookup unavailable")
    def test_cleanup_defers_without_stopping_a_running_application(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=self.src)
        try:
            with self.assertRaisesRegex(RuntimeError, "cleanup deferred"):
                sync.cleanup_workspace_copy(self.dst, self.src)
            self.assertIsNone(child.poll())
            self.assertTrue((self.src / ".gitignore").exists())
        finally:
            child.terminate()
            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
