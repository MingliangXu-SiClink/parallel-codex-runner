import concurrent.futures
import base64
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core.codex_model_cache import (
    MODEL_CACHE_FILENAME,
    CodexModelCacheManager,
    valid_model_cache,
    valid_model_catalog,
)


def write_model_cache(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "client_version": version,
                "fetched_at": dt.datetime.now(dt.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "models": [{"slug": "test-model"}],
            }
        ),
        encoding="utf-8",
    )


class CodexModelCacheTests(unittest.TestCase):
    def test_oauth_rotation_keeps_catalog_but_other_users_and_accounts_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "real"
            home.mkdir()
            manager = CodexModelCacheManager(sys.executable, home, lambda *_args: None, cache_root=root / "cache")

            def identity(account, subject, generation):
                payload = base64.urlsafe_b64encode(json.dumps({"sub": subject, "iss": "provider", "iat": generation}).encode()).decode().rstrip("=")
                (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
                    "account_id": account, "id_token": "header." + payload + ".signature",
                    "access_token": f"access-{generation}", "refresh_token": f"refresh-{generation}",
                }}))
                return manager._persistent_snapshot_path("0.159.0")

            first = identity("account", "user", 1)
            self.assertEqual(identity("account", "user", 2), first)
            self.assertNotEqual(identity("other-account", "user", 2), first)
            self.assertNotEqual(identity("account", "other-user", 2), first)

    def test_failed_refresh_preserves_stale_same_cli_source_without_reforging_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "real"
            source = home / MODEL_CACHE_FILENAME
            write_model_cache(source, "0.159.0")
            payload = json.loads(source.read_text())
            payload["fetched_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
            source.write_text(json.dumps(payload))
            before = source.read_bytes()
            manager = CodexModelCacheManager(sys.executable, home, lambda *_args: None, cache_root=root / "cache")
            manager._client_version = "0.159.0"
            with mock.patch.object(manager, "_prewarm_locked", return_value=None) as prewarm:
                self.assertTrue(manager.populate(root / "agent"))
                snapshot = manager.catalog_path()
                self.assertEqual(prewarm.call_count, 1)
            self.assertEqual(snapshot.read_bytes(), before)
            self.assertEqual((root / "agent" / MODEL_CACHE_FILENAME).read_bytes(), before)
            self.assertEqual(source.read_bytes(), before)

    def test_named_cli_has_one_cache_identity_across_working_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "bin" / "codex"
            executable.parent.mkdir()
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            original_cwd = Path.cwd()
            try:
                paths = []
                with mock.patch.dict(os.environ, {"PATH": str(executable.parent)}):
                    for name in ("daemon", "worker"):
                        directory = root / name
                        directory.mkdir()
                        os.chdir(directory)
                        manager = CodexModelCacheManager(
                            "codex", root / "real", lambda *_args: None,
                            cache_root=root / "cache",
                        )
                        self.assertEqual(manager.codex_bin, str(executable))
                        paths.append(manager._persistent_snapshot_path("0.159.0"))
                self.assertEqual(paths[0], paths[1])
            finally:
                os.chdir(original_cwd)

    def test_catalog_read_does_not_rewrite_the_shared_codex_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "real"
            source = home / MODEL_CACHE_FILENAME
            write_model_cache(source, "0.153.4")
            before = source.read_bytes()
            manager = CodexModelCacheManager(
                sys.executable, home, lambda *_args: None, cache_root=root / "cache",
            )
            manager._client_version = "0.159.0"

            def refresh(version: str, snapshot: Path) -> bool:
                write_model_cache(snapshot, version)
                payload = json.loads(snapshot.read_text())
                payload.update(pcr_catalog_schema=1, source="model/list")
                snapshot.write_text(json.dumps(payload))
                return True

            with mock.patch.object(manager, "_refresh_catalog_locked", side_effect=refresh):
                catalog = manager.catalog_path()
            self.assertIsNotNone(catalog)
            self.assertTrue(valid_model_catalog(catalog, "0.159.0"))
            self.assertFalse(valid_model_cache(catalog, "0.159.0"))
            self.assertEqual(source.read_bytes(), before)

    def test_failed_catalog_refresh_keeps_its_own_version_matched_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CodexModelCacheManager(
                sys.executable, root / "real", lambda *_args: None,
                cache_root=root / "cache",
            )
            manager._client_version = "0.159.0"
            snapshot = manager._persistent_snapshot_path("0.159.0")
            write_model_cache(snapshot, "0.159.0")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            payload["fetched_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            write_model_cache(root / "real" / MODEL_CACHE_FILENAME, "0.153.4")

            with mock.patch.object(manager, "_prewarm_locked", return_value=None):
                self.assertEqual(manager.catalog_path(), snapshot)
            manager._client_version = "0.160.0"
            with mock.patch.object(manager, "_prewarm_locked", return_value=None):
                self.assertIsNone(manager.catalog_path())

    def test_changed_catalog_context_does_not_reuse_the_old_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "real"
            home.mkdir()
            config = home / "config.toml"
            config.write_text('model_provider = "first"', encoding="utf-8")
            manager = CodexModelCacheManager(
                sys.executable, home, lambda *_args: None, cache_root=root / "cache",
            )
            manager._client_version = "0.159.0"
            first = manager._persistent_snapshot_path("0.159.0")
            write_model_cache(first, "0.159.0")
            self.assertEqual(manager.catalog_path(), first)
            config.write_text('model_provider = "second"', encoding="utf-8")
            with mock.patch.object(manager, "_prewarm_locked", return_value=None):
                self.assertIsNone(manager.catalog_path())

    def test_shared_cache_replacement_during_copy_preserves_the_good_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "real"
            manager = CodexModelCacheManager(
                sys.executable, home, lambda *_args: None, cache_root=root / "cache",
            )
            manager._client_version = "0.159.0"
            snapshot = manager._persistent_snapshot_path("0.159.0")
            write_model_cache(snapshot, "0.159.0")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            payload["fetched_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            before = snapshot.read_bytes()
            write_model_cache(home / MODEL_CACHE_FILENAME, "0.159.0")

            def replaced_source(_source: Path, destination: Path) -> None:
                write_model_cache(destination, "0.153.4")

            with mock.patch("parallel_codex_runner_core.codex_model_cache.shutil.copy2", side_effect=replaced_source), \
                 mock.patch.object(manager, "_prewarm_locked", return_value=None):
                self.assertEqual(manager.catalog_path(), snapshot)
            self.assertEqual(snapshot.read_bytes(), before)

    def test_successful_catalog_refresh_can_remove_a_previously_listed_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CodexModelCacheManager(
                sys.executable, root / "real", lambda *_args: None,
                cache_root=root / "cache",
            )
            manager._client_version = "0.159.0"
            snapshot = manager._persistent_snapshot_path("0.159.0")
            write_model_cache(snapshot, "0.159.0")
            self.assertEqual(manager.catalog_path(), snapshot)
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            payload["fetched_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
            snapshot.write_text(json.dumps(payload), encoding="utf-8")

            def refresh(version: str, destination: Path) -> bool:
                write_model_cache(destination, version)
                payload = json.loads(destination.read_text(encoding="utf-8"))
                payload["models"] = [{"slug": "replacement-model"}]
                payload.update(pcr_catalog_schema=1, source="model/list")
                destination.write_text(json.dumps(payload), encoding="utf-8")
                return True

            with mock.patch.object(manager, "_refresh_catalog_locked", side_effect=refresh):
                current = manager.catalog_path()
            self.assertEqual([item["slug"] for item in json.loads(current.read_text())["models"]],
                             ["replacement-model"])

    def test_valid_model_cache_requires_matching_fresh_nonempty_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / MODEL_CACHE_FILENAME
            write_model_cache(cache, "0.147.0")

            self.assertTrue(valid_model_cache(cache, "0.147.0"))
            self.assertFalse(valid_model_cache(cache, "0.149.0"))

            value = json.loads(cache.read_text(encoding="utf-8"))
            value["models"] = []
            cache.write_text(json.dumps(value), encoding="utf-8")
            self.assertFalse(valid_model_cache(cache, "0.147.0"))

    def test_populate_copies_matching_real_home_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_home = root / "real"
            agent_home = root / "agent"
            cache_root = root / "cache"
            write_model_cache(real_home / MODEL_CACHE_FILENAME, "0.147.0")
            manager = CodexModelCacheManager(
                sys.executable,
                real_home,
                lambda _source, destination: destination.mkdir(parents=True, exist_ok=True),
                cache_root=cache_root,
            )
            manager._client_version = "0.147.0"

            self.assertTrue(manager.populate(agent_home))
            self.assertTrue(
                valid_model_cache(agent_home / MODEL_CACHE_FILENAME, "0.147.0")
            )

    def test_populate_rejects_mismatched_real_home_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_home = root / "real"
            write_model_cache(real_home / MODEL_CACHE_FILENAME, "0.149.0")
            manager = CodexModelCacheManager(
                sys.executable,
                real_home,
                lambda _source, destination: destination.mkdir(parents=True, exist_ok=True),
                cache_root=root / "cache",
            )
            manager._client_version = "0.147.0"

            with mock.patch.object(manager, "_prewarm_locked", return_value=None) as prewarm:
                self.assertFalse(manager.populate(root / "agent"))

            prewarm.assert_called_once()

    def test_cache_failure_does_not_block_agent_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_cache_root = root / "cache-is-a-file"
            invalid_cache_root.write_text("not a directory", encoding="utf-8")
            manager = CodexModelCacheManager(
                sys.executable,
                root / "real",
                lambda _source, destination: destination.mkdir(parents=True, exist_ok=True),
                cache_root=invalid_cache_root,
            )
            manager._client_version = "0.147.0"

            self.assertFalse(manager.populate(root / "agent"))

    def test_failed_discovery_is_throttled_without_removing_the_previous_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "real" / MODEL_CACHE_FILENAME
            write_model_cache(previous, "0.153.4")
            before = previous.read_bytes()
            manager = CodexModelCacheManager(
                sys.executable, root / "real", lambda *_args: None,
                cache_root=root / "cache",
            )
            manager._client_version = "0.159.0"
            with mock.patch.object(manager, "_prewarm_locked", return_value=None) as prewarm:
                self.assertFalse(manager.populate(root / "real"))
                self.assertFalse(manager.populate(root / "real"))
                prewarm.assert_called_once()
            self.assertEqual(previous.read_bytes(), before)

    def test_replaced_execution_cli_invalidates_the_cached_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "codex"
            executable.write_text("old", encoding="utf-8")
            manager = CodexModelCacheManager(
                str(executable), root / "real", lambda *_args: None,
                cache_root=root / "cache",
            )
            with mock.patch("subprocess.run") as run:
                run.return_value.stdout = "codex-cli 0.153.4"
                run.return_value.stderr = ""
                self.assertEqual(manager._version_locked(), "0.153.4")
                self.assertEqual(manager._version_locked(), "0.153.4")
                self.assertEqual(run.call_count, 1)
                manager._snapshot_path = root / "old-snapshot"
                manager._retry_after = time.monotonic() + 30
                replacement = root / "codex-new"
                replacement.write_text("new CLI release", encoding="utf-8")
                replacement.replace(executable)
                run.return_value.stdout = "codex-cli 0.159.0"
                self.assertEqual(manager._version_locked(), "0.159.0")
                self.assertEqual(run.call_count, 2)
                self.assertIsNone(manager._snapshot_path)
                self.assertEqual(manager._retry_after, 0)

    def test_transient_version_failure_does_not_poison_catalog_discovery_forever(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "codex"
            executable.write_text("CLI", encoding="utf-8")
            manager = CodexModelCacheManager(str(executable), root / "real", lambda *_args: None,
                cache_root=root / "cache")

            def refresh(version, path):
                write_model_cache(path, version)
                payload = json.loads(path.read_text())
                payload.update(pcr_catalog_schema=1, source="model/list")
                path.write_text(json.dumps(payload))
                return True

            with mock.patch("subprocess.run", side_effect=[
                subprocess.TimeoutExpired([str(executable), "--version"], 5),
                subprocess.CompletedProcess([], 0, "codex-cli 0.159.2", ""),
            ]) as run, mock.patch("parallel_codex_runner_core.codex_model_cache.time.monotonic", return_value=100) as clock, \
                    mock.patch.object(manager, "_refresh_catalog_locked", side_effect=refresh):
                self.assertIsNone(manager.catalog_path())
                self.assertIsNone(manager.catalog_path())
                self.assertEqual(run.call_count, 1, "Failed discovery must back off rather than spawn per click")
                clock.return_value = 131
                recovered = manager.catalog_path()
                self.assertIsNotNone(recovered, "The same live manager must retry after a transient CLI failure")
                self.assertTrue(valid_model_catalog(recovered, "0.159.2"))
                self.assertEqual(run.call_count, 2)

    def test_replacing_a_failed_cli_bypasses_only_its_old_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "codex"
            executable.write_text("old CLI", encoding="utf-8")
            manager = CodexModelCacheManager(str(executable), root / "real", lambda *_args: None,
                cache_root=root / "cache")
            with mock.patch("subprocess.run", side_effect=[OSError("temporarily unavailable"),
                    subprocess.CompletedProcess([], 0, "codex-cli 0.159.2", "")]) as run:
                self.assertIsNone(manager._version_locked())
                replacement = root / "new-cli"
                replacement.write_text("replacement CLI", encoding="utf-8")
                replacement.replace(executable)
                self.assertEqual(manager._version_locked(), "0.159.2")
                self.assertEqual(run.call_count, 2)

    def test_concurrent_populate_runs_only_one_prewarm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CodexModelCacheManager(
                sys.executable,
                root / "real",
                lambda _source, destination: destination.mkdir(parents=True, exist_ok=True),
                cache_root=root / "cache",
            )
            manager._client_version = "0.147.0"
            calls = 0
            calls_lock = threading.Lock()

            def prewarm(version: str, snapshot: Path) -> Path:
                nonlocal calls
                with calls_lock:
                    calls += 1
                time.sleep(0.05)
                write_model_cache(snapshot, version)
                return snapshot

            with mock.patch.object(manager, "_prewarm_locked", side_effect=prewarm):
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    outcomes = list(
                        pool.map(
                            manager.populate,
                            [root / f"agent-{idx}" for idx in range(8)],
                        )
                    )

            self.assertEqual(calls, 1)
            self.assertEqual(outcomes, [True] * 8)

    @unittest.skipUnless(os.name == "posix", "executable test helper requires POSIX")
    def test_prewarm_cleans_temporary_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "fake-codex"
            script = root / "fake_codex.py"
            script.write_text(
                "\n".join(
                    (
                        "import datetime as dt",
                        "import json",
                        "import os",
                        "import pathlib",
                        "import sys",
                        "import time",
                        "if '--version' in sys.argv:",
                        "    print('codex-cli 0.147.0')",
                        "    raise SystemExit(0)",
                        "home = pathlib.Path(os.environ['CODEX_HOME'])",
                        "payload = {",
                        "    'client_version': '0.147.0',",
                        "    'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),",
                        "    'models': [{'slug': 'test-model'}],",
                        "}",
                        "(home / 'models_cache.json').write_text(json.dumps(payload))",
                        "time.sleep(60)",
                    )
                ),
                encoding="utf-8",
            )
            executable.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n',
                encoding="utf-8",
            )
            executable.chmod(0o755)
            cache_root = root / "cache"
            manager = CodexModelCacheManager(
                str(executable),
                root / "real",
                lambda _source, destination: destination.mkdir(parents=True, exist_ok=True),
                cache_root=cache_root,
                prewarm_timeout_seconds=3,
            )

            self.assertTrue(manager.populate(root / "agent"))
            self.assertEqual(list(cache_root.glob("prewarm-*")), [])


if __name__ == "__main__":
    unittest.main()
