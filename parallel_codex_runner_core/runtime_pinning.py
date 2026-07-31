"""Keep a running PCR process on the modules it loaded at startup."""

from __future__ import annotations

import importlib
import sys
import threading
from types import ModuleType
from typing import Callable


_PROTECTED_PREFIXES = (
    "parallel_codex_runner",
    "parallel_codex_runner_core",
    "textual",
)
_TUI_MODULES = (
    "parallel_codex_runner_core.app",
    "parallel_codex_runner_core.codex_cli",
    "parallel_codex_runner_core.codex_models",
    "parallel_codex_runner_core.diffing",
    "parallel_codex_runner_core.models",
    "parallel_codex_runner_core.paths",
    "parallel_codex_runner_core.prompt_history",
    "parallel_codex_runner_core.synthesis",
    "parallel_codex_runner_core.workspace",
    "parallel_codex_runner_core.workspace_config",
    "textual.app",
    "textual.color",
    "textual.command",
    "textual.containers",
    "textual.content",
    "textual.css.query",
    "textual.events",
    "textual.geometry",
    "textual.message",
    "textual.reactive",
    "textual.screen",
    "textual.scroll_view",
    "textual.widget",
    "textual.widgets._button",
    "textual.widgets._input",
    "textual.widgets._select",
    "textual.widgets._static",
    "textual.widgets._text_area",
)
_PLUGIN_WORKER_MODULES = (
    "parallel_codex_runner_core.app",
    "parallel_codex_runner_core.codex_cli",
    "parallel_codex_runner_core.codex_models",
    "parallel_codex_runner_core.diffing",
    "parallel_codex_runner_core.models",
    "parallel_codex_runner_core.paths",
    "parallel_codex_runner_core.plugin.artifacts",
    "parallel_codex_runner_core.plugin.events",
    "parallel_codex_runner_core.plugin.lifecycle",
    "parallel_codex_runner_core.plugin.state",
    "parallel_codex_runner_core.plugin_runtime",
    "parallel_codex_runner_core.synthesis",
    "parallel_codex_runner_core.workspace",
)
_lock = threading.Lock()
_reload_guard_installed = False
_original_reload: Callable[[ModuleType], ModuleType] = importlib.reload


def _is_protected_module(name: str) -> bool:
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in _PROTECTED_PREFIXES)


def install_reload_guard() -> None:
    """Reject explicit reloads of code owned by an active PCR process."""
    global _reload_guard_installed

    with _lock:
        if _reload_guard_installed:
            return

        def guarded_reload(module: ModuleType) -> ModuleType:
            name = str(getattr(module, "__name__", ""))
            if _is_protected_module(name):
                raise RuntimeError(
                    f"Cannot reload {name} inside a running PCR process; restart PCR "
                    "to use source changes."
                )
            return _original_reload(module)

        importlib.reload = guarded_reload
        _reload_guard_installed = True


def _preload(module_names: tuple[str, ...]) -> tuple[str, ...]:
    for name in module_names:
        importlib.import_module(name)
    install_reload_guard()
    return tuple(
        sorted(name for name in sys.modules if _is_protected_module(name))
    )


def preload_tui_runtime() -> tuple[str, ...]:
    """Eagerly import the PCR and Textual modules used by the TUI."""
    from ._vendor import activate_textual

    activate_textual()
    return _preload(_TUI_MODULES)


def preload_plugin_worker_runtime() -> tuple[str, ...]:
    """Eagerly import modules a persistent plugin worker may need later."""
    return _preload(_PLUGIN_WORKER_MODULES)
