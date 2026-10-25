import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core import codex_models
from parallel_codex_runner_core.codex_models import CodexModelRegistry
from parallel_codex_runner_core.plugin.state import JsonStateStore, ManagedRun, STATE_VERSION


class ConfigDefaultsCacheTests(unittest.TestCase):
    def setUp(self):
        codex_models._read_config_defaults.cache_clear()

    def tearDown(self):
        codex_models._read_config_defaults.cache_clear()

    def test_unchanged_config_is_parsed_once_without_caching_model_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.toml").write_text(
                'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "HIGH"\nservice_tier = "fast"\n'
            )
            catalog = home / "models_cache.json"
            catalog.write_text(json.dumps({"models": [{
                "slug": "gpt-6.1-sol", "supported_reasoning_efforts": ["high"],
            }]}))
            with mock.patch.object(codex_models.tomllib, "load", wraps=codex_models.tomllib.load) as parse:
                for _ in range(4):
                    registry = CodexModelRegistry.load(home)
                    self.assertEqual(registry.resolve_effort(None, None), "high")
                    self.assertEqual(registry.configured_service_tier, "fast")
                    with self.assertRaises(ValueError):
                        registry.validate_effort(None, "max")
                self.assertEqual(parse.call_count, 1)
                catalog.write_text(json.dumps({"models": [{
                    "slug": "gpt-6.1-sol", "supported_reasoning_efforts": ["max"],
                }]}))
                self.assertEqual(CodexModelRegistry.load(home).resolve_effort(None, "max"), "max")
                self.assertEqual(parse.call_count, 1)

    def test_in_place_change_with_restored_mtime_is_not_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('model_reasoning_effort = "high"\n')
            before = path.stat()
            self.assertEqual(codex_models._load_config_defaults(path)[1], "high")
            path.write_text('model_reasoning_effort = "max "\n')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(path.stat().st_size, before.st_size)
            self.assertEqual(codex_models._load_config_defaults(path)[1], "max")

    def test_atomic_replacement_and_home_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.toml"
            second = root / "second.toml"
            first.write_text('model = "first"\n')
            second.write_text('model = "other"\n')
            self.assertEqual(codex_models._load_config_defaults(first)[0], "first")
            self.assertEqual(codex_models._load_config_defaults(second)[0], "other")
            before = first.stat()
            os.utime(second, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(second, first)
            self.assertEqual(codex_models._load_config_defaults(first)[0], "other")

    def test_missing_or_malformed_config_does_not_poison_repaired_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            self.assertEqual(codex_models._load_config_defaults(path), (None, None, None))
            path.write_text('model = "unterminated\n')
            self.assertEqual(codex_models._load_config_defaults(path), (None, None, None))
            path.write_text('model = "gpt-6.1-sol"\n')
            self.assertEqual(codex_models._load_config_defaults(path)[0], "gpt-6.1-sol")

    def test_transient_read_failure_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('model = "gpt-6.1-sol"\n')
            with mock.patch.object(Path, "open", side_effect=PermissionError):
                self.assertEqual(codex_models._load_config_defaults(path), (None, None, None))
            self.assertEqual(codex_models._load_config_defaults(path)[0], "gpt-6.1-sol")

    def test_change_during_parse_retries_without_caching_old_payload_as_new(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('model = "first"\n')
            parse = codex_models.tomllib.load
            calls = 0

            def changing_parse(handle):
                nonlocal calls
                payload = parse(handle)
                calls += 1
                if calls == 1:
                    path.write_text('model = "second"\n')
                return payload

            with mock.patch.object(codex_models.tomllib, "load", side_effect=changing_parse):
                self.assertEqual(codex_models._load_config_defaults(path)[0], "second")
                self.assertEqual(calls, 2)
                self.assertEqual(codex_models._load_config_defaults(path)[0], "second")
                self.assertEqual(calls, 2)

    def test_config_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index in range(24):
                path = Path(temporary) / f"config-{index}.toml"
                path.write_text(f'model = "model-{index}"\n')
                self.assertEqual(codex_models._load_config_defaults(path)[0], f"model-{index}")
            self.assertEqual(codex_models._read_config_defaults.cache_info().currsize, 16)


class SubmissionStatePersistenceTests(unittest.TestCase):
    def test_compact_state_preserves_schema_order_unicode_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JsonStateStore(Path(temporary))
            first = ManagedRun("first", "\u7ee7\u7eed\u63d0\u95ee\nline 2", "/workspace", {"model": "gpt-6.1-sol"},
                               turn_id="turn-first", created_at="2026-10-04T01:00:00", agent_statuses={1: "succeeded"})
            second = ManagedRun("second", "next", "/workspace", {}, turn_id="turn-second", created_at="2026-10-04T02:00:00")
            with store.locked():
                with mock.patch("parallel_codex_runner_core.plugin.state.os.fsync", wraps=os.fsync) as sync:
                    store.write_runs({"second": second, "first": first})
                    self.assertEqual(sync.call_count, 1)
            payload = store.load_payload()
            self.assertEqual(payload["version"], STATE_VERSION)
            self.assertEqual([item["run_id"] for item in payload["runs"]], ["first", "second"])
            self.assertEqual(ManagedRun.from_dict(payload["runs"][0]).to_dict(), first.to_dict())
            self.assertEqual(ManagedRun.from_dict(payload["runs"][1]).to_dict(), second.to_dict())
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.path.read_text().count("\n"), 1)
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_failed_commit_preserves_previous_state_and_removes_temporary_file(self):
        for failing_operation in ("fsync", "replace"):
            with self.subTest(operation=failing_operation), tempfile.TemporaryDirectory() as temporary:
                store = JsonStateStore(Path(temporary))
                original = ManagedRun("original", "saved", "/workspace", {})
                with store.locked():
                    store.write_runs({"original": original})
                    before = store.path.read_bytes()
                    with mock.patch(f"parallel_codex_runner_core.plugin.state.os.{failing_operation}", side_effect=OSError):
                        with self.assertRaises(OSError):
                            store.write_runs({"new": ManagedRun("new", "new", "/workspace", {})})
                    self.assertEqual(store.path.read_bytes(), before)
                    self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_encoding_failure_leaves_no_partial_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JsonStateStore(Path(temporary))
            with store.locked():
                with self.assertRaises(TypeError):
                    store.write_runs({"bad": ManagedRun("bad", "bad", "/workspace", {"bad": object()})})
            self.assertFalse(store.path.exists())
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
