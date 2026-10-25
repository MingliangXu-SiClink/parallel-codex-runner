import tempfile
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

from parallel_codex_runner_core.conversation_search import matching_excerpt, search_file
from parallel_codex_runner_core.plugin_runtime import PluginRunManager


class ConversationSearchTests(unittest.TestCase):
    def test_search_scopes_workspace_and_preserves_cursor(self):
        with tempfile.TemporaryDirectory() as root:
            answer = Path(root) / "answer.md"
            answer.write_text("match only in saved answer", encoding="utf-8")
            manager = SimpleNamespace(
                list_runs=Mock(return_value={"runs": [{"run_id": "r", "prompt": "a question"}],
                    "next_cursor": "next", "has_more": True}),
                _lock=threading.RLock(),
                _require_run_locked=Mock(return_value=SimpleNamespace(results={2: {}}, agent_statuses={2: "success"})),
                _agent_meta_file=Mock(return_value=answer),
            )
            result = PluginRunManager.search_conversation_text(manager, query="saved answer", workspace="scoped", cursor="page")
            self.assertEqual(result["matches"][0]["agent"], 2)
            self.assertEqual(result["next_cursor"], "next")
            manager.list_runs.assert_called_once_with(limit=10, cursor="page", workspace="scoped")

    def test_literal_unicode_and_casefold(self):
        self.assertIn("中文内容", matching_excerpt("start 中文内容 end", "中文内容"))
        self.assertIn("Straße", matching_excerpt("Straße", "STRASSE"))
        self.assertIsNone(matching_excerpt("hello", ".*"))

    def test_search_across_chunk_and_multibyte_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "answer.md"
            path.write_text("x" * 65534 + "中文检索" + "y" * 100000, encoding="utf-8")
            self.assertIn("中文检索", search_file(path, "中文检索"))
            self.assertIsNone(search_file(path, "not present"))
            self.assertIsNone(search_file(Path(root) / "missing", "query"))
