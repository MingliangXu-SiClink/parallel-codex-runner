import tempfile
import unittest
from pathlib import Path

from parallel_codex_runner import build_codex_command, create_unique_run_root, sync_back_with_python


class SyncBackTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
