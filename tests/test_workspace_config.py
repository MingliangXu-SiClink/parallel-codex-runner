import argparse
import tempfile
import unittest
from pathlib import Path

from parallel_codex_runner_core.workspace_config import (
    WorkspaceConfigStore,
    WorkspaceSettings,
)


class WorkspaceConfigTests(unittest.TestCase):
    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(
            num_agents=4,
            synthesis_agents=2,
            serial=False,
            max_parallel=None,
            subagents=False,
            subagents_limit=8,
            recommend_by="reasoning_tokens",
            model=None,
            effort=None,
            no_sync_back=False,
            keep_workspaces=False,
            resume_session_id=None,
        )

    def test_settings_are_saved_per_workspace_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = WorkspaceConfigStore(root / "settings.json")
            workspace_a = root / "project-a"
            workspace_b = root / "project-b"
            workspace_a.mkdir()
            workspace_b.mkdir()

            args = self._args()
            args.num_agents = 7
            args.synthesis_agents = 3
            args.serial = True
            args.subagents = True
            args.subagents_limit = 12
            args.recommend_by = "duration"
            args.model = "gpt-test"
            args.effort = "high"
            args.no_sync_back = True
            args.keep_workspaces = True
            settings = WorkspaceSettings.from_runtime(7, 3, args, "session-a")

            self.assertTrue(store.save(workspace_a, settings))
            loaded = store.load(workspace_a)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.to_mapping(), settings.to_mapping())
            self.assertIsNone(store.load(workspace_b))

            restored = self._args()
            loaded.apply_to_args(restored)
            self.assertEqual(restored.num_agents, 7)
            self.assertEqual(restored.synthesis_agents, 3)
            self.assertTrue(restored.serial)
            self.assertEqual(restored.max_parallel, 1)
            self.assertTrue(restored.subagents)
            self.assertEqual(restored.subagents_limit, 12)
            self.assertEqual(restored.recommend_by, "duration")
            self.assertEqual(restored.model, "gpt-test")
            self.assertEqual(restored.effort, "high")
            self.assertTrue(restored.no_sync_back)
            self.assertTrue(restored.keep_workspaces)
            self.assertEqual(restored.resume_session_id, "session-a")

    def test_saved_auto_and_default_values_clear_previous_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = WorkspaceConfigStore(root / "settings.json")
            workspace = root / "project"
            workspace.mkdir()
            args = self._args()
            store.save(workspace, WorkspaceSettings.from_runtime(4, 2, args, ""))

            restored = self._args()
            restored.model = "old-model"
            restored.effort = "high"
            restored.resume_session_id = "old-session"
            loaded = store.load(workspace)
            self.assertIsNotNone(loaded)
            loaded.apply_to_args(restored)
            self.assertIsNone(restored.model)
            self.assertIsNone(restored.effort)
            self.assertIsNone(restored.resume_session_id)

    def test_explicit_cli_settings_win_over_saved_values(self) -> None:
        saved = WorkspaceSettings.from_mapping(
            {
                "AGENTS": 8,
                "EXECUTION": "serial",
                "MAX_PARALLEL": 1,
                "MODEL": "saved-model",
                "RESUME": "saved-session",
            }
        )
        self.assertIsNotNone(saved)
        args = self._args()
        args.num_agents = 5
        args.model = "cli-model"
        args.resume_session_id = "cli-session"
        saved.apply_to_args(args, {"agents", "model", "resume"})
        self.assertEqual(args.num_agents, 5)
        self.assertEqual(args.model, "cli-model")
        self.assertEqual(args.resume_session_id, "cli-session")
        self.assertTrue(args.serial)

    def test_legacy_display_values_are_read(self) -> None:
        settings = WorkspaceSettings.from_mapping(
            {
                "AGENTS": 4,
                "EXECUTION": "parallel",
                "MAX_PARALLEL": "auto",
                "SUBAGENTS": "NO",
                "MODEL": "default",
                "EFFORT": "auto",
                "SYNC_BACK": "YES",
                "KEEP_WORKSPACES": "NO",
                "RESUME": "NO",
            }
        )
        self.assertIsNotNone(settings)
        self.assertFalse(settings.subagents)
        self.assertIsNone(settings.model)
        self.assertIsNone(settings.effort)
        self.assertTrue(settings.sync_back)
        self.assertFalse(settings.keep_workspaces)
        self.assertIsNone(settings.resume_session_id)


if __name__ == "__main__":
    unittest.main()
