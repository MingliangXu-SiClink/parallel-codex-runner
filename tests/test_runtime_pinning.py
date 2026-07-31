import importlib
import types
import unittest
from unittest import mock

from parallel_codex_runner_core import app
from parallel_codex_runner_core import runtime_pinning


class RuntimePinningTests(unittest.TestCase):
    def test_tui_preload_imports_required_runtime_modules(self) -> None:
        loaded = runtime_pinning.preload_tui_runtime()

        self.assertIn("parallel_codex_runner_core.app", loaded)
        self.assertIn("textual.app", loaded)
        self.assertIn("textual.widgets._text_area", loaded)

    def test_reload_guard_rejects_pcr_modules(self) -> None:
        runtime_pinning.install_reload_guard()

        with self.assertRaisesRegex(RuntimeError, "restart PCR"):
            importlib.reload(app)

    def test_reload_guard_delegates_unrelated_modules(self) -> None:
        runtime_pinning.install_reload_guard()
        module = types.ModuleType("example_unrelated_module")

        with mock.patch.object(
            runtime_pinning,
            "_original_reload",
            return_value=module,
        ) as original_reload:
            self.assertIs(importlib.reload(module), module)

        original_reload.assert_called_once_with(module)


if __name__ == "__main__":
    unittest.main()
