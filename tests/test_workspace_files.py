import base64
import tempfile
import unittest
from pathlib import Path

from parallel_codex_runner_core.remote.workspace_files import (
    WorkspaceFileContext,
    WorkspaceFileError,
    read_workspace_file,
    resolve_workspace_item,
)


class WorkspaceFileTests(unittest.TestCase):
    def test_literal_source_links_do_not_depend_on_candidate_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "source"
            workspace.mkdir()
            (workspace / "answer.md").write_text("source", encoding="utf-8")
            candidate = root / "deleted-candidate"
            context = WorkspaceFileContext(workspace, candidate, candidate, True, literal_paths=True)
            page = read_workspace_file(context, str(workspace / "answer.md"))
            self.assertEqual(base64.b64decode(page["content_base64"]), b"source")
            for reference in ("answer.md", str(candidate / "answer.md")):
                with self.subTest(reference=reference), self.assertRaisesRegex(WorkspaceFileError, "no longer available"):
                    read_workspace_file(context, reference)

    def test_source_mode_and_folders_use_the_same_verified_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "source"
            folder = workspace / "outputs" / "videos_60fps"
            folder.mkdir(parents=True)
            (folder / "clip.mp4").write_bytes(b"video")
            (folder / "outside").symlink_to(root)
            context = WorkspaceFileContext(workspace, workspace, workspace, literal_paths=True)
            result = resolve_workspace_item(context, str(folder))
            self.assertEqual(result["kind"], "directory")
            self.assertEqual(result["scope"], "source")
            self.assertEqual(result["path"], str(folder.resolve()))
            self.assertEqual([entry["name"] for entry in result["entries"]], ["clip.mp4"])
            self.assertEqual(resolve_workspace_item(context, ".")["kind"], "directory")
            for reference in ("..", str(folder / "outside")):
                with self.assertRaises(WorkspaceFileError):
                    resolve_workspace_item(context, reference)

    def test_directory_pages_and_candidate_identity_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "source"
            candidate = root / "run" / "agent_001"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            for i in range(205):
                (candidate / f"file-{i:03d}.txt").write_text(str(i), encoding="utf-8")
            context = WorkspaceFileContext(workspace, candidate, candidate, literal_paths=True)
            first = resolve_workspace_item(context, ".")
            second = resolve_workspace_item(context, ".", offset=int(first["next_offset"]))
            self.assertEqual(first["scope"], "candidate")
            self.assertEqual(len(first["entries"]), 200)
            self.assertTrue(first["has_more"])
            self.assertEqual(len(second["entries"]), 5)
            self.assertFalse(second["has_more"])

    def test_reads_stable_bounded_pages_and_preserves_line_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            target = workspace / "notes.md"
            target.write_bytes(b"abcdefgh")
            context = WorkspaceFileContext(workspace_root=workspace)

            first = read_workspace_file(
                context,
                f"{target}:7",
                max_bytes=3,
            )
            second = read_workspace_file(
                context,
                str(target),
                offset=int(first["next_offset"]),
                max_bytes=16,
                expected_file_id=str(first["file_id"]),
            )

            self.assertEqual(base64.b64decode(first["content_base64"]), b"abc")
            self.assertEqual(base64.b64decode(second["content_base64"]), b"defgh")
            self.assertEqual(first["line"], 7)
            self.assertTrue(first["has_more"])
            self.assertFalse(second["has_more"])
            self.assertEqual(first["relative_path"], "notes.md")

    def test_rejects_workspace_escape_and_outside_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "private.txt"
            outside.write_text("secret", encoding="utf-8")
            (workspace / "escape.txt").symlink_to(outside)
            context = WorkspaceFileContext(workspace_root=workspace)

            for reference in ("../private.txt", str(outside), "escape.txt"):
                with self.subTest(reference=reference):
                    with self.assertRaises(WorkspaceFileError):
                        read_workspace_file(context, reference)

    def test_uses_agent_copy_then_maps_cleaned_finalized_copy_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "run" / "workspaces" / "agent_002"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            (workspace / "answer.md").write_text("accepted", encoding="utf-8")
            candidate_file = candidate / "answer.md"
            candidate_file.write_text("candidate", encoding="utf-8")

            live = read_workspace_file(
                WorkspaceFileContext(
                    workspace_root=workspace,
                    candidate_root=candidate,
                    expected_candidate_root=candidate,
                ),
                str(candidate_file),
            )
            self.assertEqual(base64.b64decode(live["content_base64"]), b"candidate")

            original_style_link = read_workspace_file(
                WorkspaceFileContext(
                    workspace_root=workspace,
                    candidate_root=candidate,
                    expected_candidate_root=candidate,
                ),
                str(workspace / "answer.md"),
            )
            self.assertEqual(
                base64.b64decode(original_style_link["content_base64"]),
                b"candidate",
            )

            candidate_file.unlink()
            candidate.rmdir()
            cleaned = read_workspace_file(
                WorkspaceFileContext(
                    workspace_root=workspace,
                    candidate_root=None,
                    expected_candidate_root=candidate,
                    candidate_synced_to_workspace=True,
                ),
                str(candidate_file),
            )
            self.assertEqual(base64.b64decode(cleaned["content_base64"]), b"accepted")

            with self.assertRaisesRegex(WorkspaceFileError, "no longer available"):
                read_workspace_file(
                    WorkspaceFileContext(
                        workspace_root=workspace,
                        expected_candidate_root=candidate,
                    ),
                    str(candidate_file),
                )

    def test_rejects_candidate_root_that_does_not_match_the_selected_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "run-a" / "workspaces" / "agent_001"
            expected = root / "run-b" / "workspaces" / "agent_001"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            expected.mkdir(parents=True)
            (candidate / "private.txt").write_text("wrong run", encoding="utf-8")

            with self.assertRaisesRegex(WorkspaceFileError, "selected run"):
                read_workspace_file(
                    WorkspaceFileContext(
                        workspace_root=workspace,
                        candidate_root=candidate,
                        expected_candidate_root=expected,
                    ),
                    "private.txt",
                )

    def test_rejects_a_file_that_changed_between_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            target = workspace / "data.bin"
            target.write_bytes(b"before")
            context = WorkspaceFileContext(workspace_root=workspace)
            first = read_workspace_file(context, "data.bin", max_bytes=2)
            target.write_bytes(b"after-change")

            with self.assertRaisesRegex(WorkspaceFileError, "changed"):
                read_workspace_file(
                    context,
                    "data.bin",
                    offset=2,
                    expected_file_id=str(first["file_id"]),
                )


if __name__ == "__main__":
    unittest.main()
