import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from parallel_codex_runner_core.visible_paths import (
    WorkspacePathMapping,
    codex_file_citations_to_markdown,
    load_session_workspace_mappings,
    record_session_workspace_mapping,
    rewrite_workspace_references,
)


class VisibleWorkspacePathTests(unittest.TestCase):
    def test_incomplete_markdown_links_cannot_stall_history_reading(self) -> None:
        script = """
from pathlib import Path
from parallel_codex_runner_core.visible_paths import WorkspacePathMapping, rewrite_workspace_references
mapping = WorkspacePathMapping.create('/unused-candidate', Path.cwd())
for body in ('a' * 50000, '\u4e2d' * 50000, '(part)' * 10000):
    text = '[report](' + body
    assert rewrite_workspace_references(text, (mapping,)) == text
"""
        # A regression in this parser must fail the test, not hang the test runner.
        subprocess.run([sys.executable, "-c", script], check=True, timeout=5)

    def test_renders_codex_file_citation_as_a_normal_local_file_link(self) -> None:
        text = (
            'Created :codex-file-citation{path="/Users/test/Project Name/result [final].docx" '
            'purpose="output"} successfully.'
        )

        rendered = codex_file_citations_to_markdown(text)

        self.assertEqual(
            "Created [result \\[final\\].docx]"
            "(<file:///Users/test/Project%20Name/result%20%5Bfinal%5D.docx>) successfully.",
            rendered,
        )

    def test_leaves_malformed_or_non_file_citation_unchanged(self) -> None:
        malformed = ':codex-file-citation{purpose="output"}'
        external = ':codex-file-citation{path="https://example.com/file.md" purpose="source"}'

        self.assertEqual(malformed, codex_file_citations_to_markdown(malformed))
        self.assertEqual(external, codex_file_citations_to_markdown(external))

    def test_session_mapping_store_is_private_and_replaces_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex"
            target = root / "workspace"
            target.mkdir()
            record_session_workspace_mapping(
                codex_home,
                "session-1",
                root / "agent-old",
                target,
            )
            record_session_workspace_mapping(
                codex_home,
                "session-1",
                root / "agent-old",
                root / "workspace-new",
            )

            mappings = load_session_workspace_mappings(codex_home, "session-1")

            self.assertEqual(len(mappings), 1)
            self.assertEqual(mappings[0].target_root, root / "workspace-new")
            stored = next((codex_home / "pcr" / "session_path_mappings").iterdir())
            self.assertEqual(stored.stat().st_mode & 0o777, 0o600)

    def test_rewrites_verified_markdown_file_and_plain_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "runs" / "workspaces" / "agent_001"
            target = root / "Project Name"
            (target / "docs").mkdir(parents=True)
            (target / "docs" / "说明 file.md").write_text("ok", encoding="utf-8")
            (target / "docs" / "plot(final).png").write_text("ok", encoding="utf-8")
            mapping = WorkspacePathMapping.create(source, target)
            encoded_source = str(source).replace(" ", "%20")
            text = "\n".join(
                (
                    f"[说明](<{source}/docs/说明 file.md#part>)",
                    f"![图](file://{encoded_source}/docs/%E8%AF%B4%E6%98%8E%20file.md)",
                    f"[结果]({source}/docs/plot(final).png \"figure\")",
                    f"`{source}/docs/说明 file.md:12`",
                )
            )

            rewritten = rewrite_workspace_references(text, (mapping,))

            self.assertIn(f"<{target}/docs/说明 file.md#part>", rewritten)
            self.assertIn("file://", rewritten)
            self.assertIn("Project%20Name/docs/%E8%AF%B4%E6%98%8E%20file.md", rewritten)
            self.assertIn(
                f'{target}/docs/plot(final).png "figure"',
                rewritten,
            )
            self.assertIn(f"`{target}/docs/说明 file.md:12`", rewritten)
            self.assertNotIn(str(source), rewritten)

    def test_does_not_rewrite_missing_external_or_similar_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "agent_001"
            target = root / "workspace"
            target.mkdir()
            mapping = WorkspacePathMapping.create(source, target)
            text = "\n".join(
                (
                    f"[missing]({source}/missing.md)",
                    f"`{source}-backup/file.md`",
                    "[web](https://example.com/project/file.md)",
                )
            )

            self.assertEqual(text, rewrite_workspace_references(text, (mapping,)))


if __name__ == "__main__":
    unittest.main()
