"""Runtime activation for privately bundled dependencies."""

from __future__ import annotations

import sys
from pathlib import Path


def activate_textual() -> Path:
    """Put PCR's vendored Textual source first on the import path."""
    package_vendor = Path(__file__).resolve().parent
    checkout_vendor = Path(__file__).resolve().parents[2] / "vendor" / "textual" / "src"

    for root in (package_vendor, checkout_vendor):
        if not (root / "textual" / "__init__.py").is_file():
            continue

        loaded = sys.modules.get("textual")
        if loaded is not None:
            loaded_file = Path(str(getattr(loaded, "__file__", ""))).resolve()
            if not loaded_file.is_relative_to(root):
                raise RuntimeError(
                    "PCR's TUI must load its vendored Textual before another Textual installation"
                )
            return root

        root_text = str(root)
        if root_text in sys.path:
            sys.path.remove(root_text)
        sys.path.insert(0, root_text)
        return root

    raise ModuleNotFoundError("PCR's vendored Textual source is missing")
